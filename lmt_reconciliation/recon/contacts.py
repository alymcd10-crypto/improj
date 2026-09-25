"""Build indexed Top Producer contacts and worklist identities."""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from . import normalize as N
from .loaders import Table


@dataclass
class Person:
    first: str
    last: str

    @property
    def first_key(self) -> str:
        return N.name_key(self.first)

    @property
    def last_key(self) -> str:
        return N.name_key(self.last)


@dataclass
class TPContact:
    tp_id: str
    row_number: int
    raw: dict
    persons: list[Person]
    company: str
    display: str
    emails: list[str]
    phones: list[tuple[str, "N.Phone"]]       # (column, parsed phone)
    categories: list[str]
    notes: str
    street: str
    zip: str
    modified: str

    @property
    def email_keys(self) -> set[str]:
        return {N.email_key(e) for e in self.emails}

    @property
    def phone_mains(self) -> set[str]:
        return {p.main for _, p in self.phones if p.main}

    @property
    def address_key(self) -> str:
        a = N.address_key(self.street)
        return f"{a}|{N.zip_key(self.zip)}" if a else ""

    @property
    def label(self) -> str:
        if self.display:
            return self.display
        names = " & ".join(p.first for p in self.persons if p.first)
        last = next((p.last for p in self.persons if p.last), "")
        text = f"{names} {last}".strip()
        return text or self.company or f"(no name, row {self.row_number})"


@dataclass
class TPExport:
    filename: str
    sha256: str
    roles: dict
    contacts: list[TPContact]
    by_id: dict[str, TPContact]            # only IDs that appear on exactly one row
    by_last: dict[str, list[TPContact]]
    by_company: dict[str, list[TPContact]]
    by_email: dict[str, list[TPContact]]
    by_phone: dict[str, list[TPContact]]
    by_address: dict[str, list[TPContact]]
    field_status: dict[str, str]          # 'present' | 'missing' | 'all_blank'
    warnings: list[str] = field(default_factory=list)
    category_separator: str = ";"
    duplicate_ids: dict[str, list[TPContact]] = field(default_factory=dict)  # ID -> every row carrying it

    def duplicate_id_reason(self, tp_id: str) -> str:
        rows = self.duplicate_ids.get(tp_id, [])
        if not rows:
            return ""
        who = "; ".join(f"row {c.row_number}: {c.label}" for c in rows[:5])
        return (f"Contact ID {tp_id} appears on {len(rows)} rows of {self.filename} ({who}). The tool cannot "
                "tell which row is the real contact, so it does not pick one. Check the export or merge the "
                "duplicate in Top Producer first.")

    def field_available(self, kind: str) -> tuple[bool, str]:
        status = self.field_status.get(kind, "missing")
        if status == "present":
            return True, ""
        if status == "all_blank":
            return False, (f"The {kind} column(s) are blank for every contact in this export, "
                           "which usually means the export left them out.")
        return False, f"This export has no {kind} column."


def _col(row: dict, header: str) -> str:
    return N.clean(row.get(header, "")) if header else ""


def detect_category_separator(values: list[str]) -> str:
    joined = "\n".join(values)
    for sep in (";", "|"):
        if sep in joined:
            return sep
    return ","


def build_tp_export(table: Table, roles: dict, filename: str, sha256: str) -> TPExport:
    contacts: list[TPContact] = []
    warnings: list[str] = list(table.warnings)
    cat_values = [_col(r, h) for r in table.rows for h in roles.get("categories", [])]
    cat_sep = detect_category_separator(cat_values)
    ext_cols = roles.get("phone_exts", [])

    for row, rn in zip(table.rows, table.row_numbers):
        persons = []
        first, last = _col(row, roles.get("first")), _col(row, roles.get("last"))
        if first or last:
            persons.append(Person(first, last))
        sf, sl = _col(row, roles.get("spouse_first")), _col(row, roles.get("spouse_last"))
        if sf or sl:
            persons.append(Person(sf, sl or last))
        display = _col(row, roles.get("display_name"))
        if not persons and display:
            persons = parse_household(display)
        emails = []
        for h in roles.get("emails", []):
            emails.extend(N.split_emails(_col(row, h)))
        phones = []
        for h in roles.get("phones", []):
            ext_val = ""
            for eh in ext_cols:  # pair "Work Phone Ext" with "Work Phone"
                if N.header_key(eh).replace("extension", "").replace("ext", "") == N.header_key(h):
                    ext_val = _col(row, eh)
            for raw in N.split_phones(_col(row, h)):
                p = N.parse_phone(raw)
                if ext_val and not p.ext:
                    p = N.Phone(raw=f"{raw} x{ext_val}", main=p.main, ext=re.sub(r"\D", "", ext_val),
                                problem=p.problem)
                phones.append((h, p))
        cats = []
        for h in roles.get("categories", []):
            cats.extend(N.split_categories(_col(row, h), cat_sep + "\n"))
        notes = "\n\n".join(row.get(h, "") for h in roles.get("notes", []) if N.clean(row.get(h, "")))
        tp_id = _col(row, roles.get("tp_id"))
        contacts.append(TPContact(
            tp_id=tp_id, row_number=rn, raw=row, persons=persons,
            company=_col(row, roles.get("company")), display=display, emails=emails,
            phones=phones, categories=cats, notes=notes, street=_col(row, roles.get("street")),
            zip=_col(row, roles.get("zip")), modified=_col(row, roles.get("modified"))))

    by_id, by_last, by_company = {}, defaultdict(list), defaultdict(list)
    by_email, by_phone, by_address = defaultdict(list), defaultdict(list), defaultdict(list)
    rows_by_id: dict[str, list[TPContact]] = defaultdict(list)
    for c in contacts:
        if c.tp_id:
            rows_by_id[c.tp_id].append(c)
    # An ID on several rows is never resolved to any one of them (neither first nor last).
    duplicate_ids = {i: rows for i, rows in rows_by_id.items() if len(rows) > 1}
    by_id = {i: rows[0] for i, rows in rows_by_id.items() if len(rows) == 1}
    for i, rows in duplicate_ids.items():
        warnings.append(f"Contact ID {i} appears on {len(rows)} rows "
                        f"(rows {', '.join(str(c.row_number) for c in rows)}). Changes for this ID are held "
                        "for review.")
    for c in contacts:
        for lk in {p.last_key for p in c.persons if p.last_key}:
            by_last[lk].append(c)
        for ck in {N.name_key(c.company)} - {""}:
            by_company[ck].append(c)
        if not any(p.first for p in c.persons):  # business stored in the last-name field
            for lk in {p.last_key for p in c.persons if p.last_key}:
                by_company[lk].append(c)
        for e in c.email_keys:
            by_email[e].append(c)
        for m in c.phone_mains:
            by_phone[m].append(c)
        if c.address_key:
            by_address[c.address_key].append(c)

    def status(cols: list[str]) -> str:
        cols = [c for c in cols if c]
        if not cols:
            return "missing"
        if table.rows and not any(N.clean(r.get(h, "")) for r in table.rows for h in cols):
            return "all_blank"
        return "present"

    field_status = {
        "phone": status(roles.get("phones", [])),
        "email": status(roles.get("emails", [])),
        "category": status(roles.get("categories", [])),
        "note": status(roles.get("notes", [])),
        "address": status([roles.get("street", "")]),
        "birthday": status([roles.get("birthday", "")]),
        "id": status([roles.get("tp_id", "")]),
    }
    if field_status["id"] != "present":
        warnings.append("No contact ID column was found. Contacts can still be matched by name plus "
                        "email/phone/address, but previously verified IDs cannot be checked.")
    return TPExport(filename=filename, sha256=sha256, roles=roles, contacts=contacts, by_id=by_id,
                    by_last=dict(by_last), by_company=dict(by_company), by_email=dict(by_email),
                    by_phone=dict(by_phone), by_address=dict(by_address), field_status=field_status,
                    warnings=warnings, category_separator=cat_sep, duplicate_ids=duplicate_ids)


# ----------------------------------------------------------------- households
_JOINERS = re.compile(r"\s+(?:&|and|\+)\s+|\s*&\s*|\s*/\s*", re.IGNORECASE)


def parse_household(text: str) -> list[Person]:
    """Split a household/display name into people without splitting the profile.

    'John & Jane Smith' -> John Smith, Jane Smith
    'Smith, John & Jane' -> John Smith, Jane Smith
    'John Smith & Jane Doe' -> John Smith, Jane Doe
    'The Smith Family' -> (no first) Smith
    """
    text = N.clean(text)
    if not text:
        return []
    fam = re.match(r"^(?:the\s+)?(.+?)\s+(?:family|household)$", text, re.IGNORECASE)
    if fam:
        return [Person("", fam.group(1))]
    if "," in text:
        last, rest = text.split(",", 1)
        firsts = [f.strip() for f in _JOINERS.split(rest) if f.strip()]
        return [Person(f, last.strip()) for f in firsts] or [Person("", last.strip())]
    parts = [p.strip() for p in _JOINERS.split(text) if p.strip()]
    people = []
    for p in parts:
        words = p.split()
        if len(words) >= 2:
            people.append(Person(" ".join(words[:-1]), words[-1]))
        else:
            people.append(Person(p, ""))
    shared_last = next((p.last for p in reversed(people) if p.last), "")
    return [Person(p.first, p.last or shared_last) for p in people]


@dataclass
class WorklistIdentity:
    key: str
    label: str
    persons: list[Person]
    company: str
    emails: list[str]
    phones: list["N.Phone"]
    address_key: str
    tp_id: str
    change_ids: list[str] = field(default_factory=list)

    @property
    def email_keys(self) -> set[str]:
        return {N.email_key(e) for e in self.emails}

    @property
    def phone_mains(self) -> set[str]:
        return {p.main for p in self.phones if p.main}


def worklist_identity(row: dict, roles: dict) -> WorklistIdentity:
    g = lambda r: N.clean(row.get(roles.get(r, ""), "")) if roles.get(r) else ""  # noqa: E731
    persons = []
    if g("first") or g("last"):
        persons.append(Person(g("first"), g("last")))
    if g("spouse_first") or g("spouse_last"):
        persons.append(Person(g("spouse_first"), g("spouse_last") or g("last")))
    household = g("household")
    if household:
        for p in parse_household(household):
            if not any(p.first_key == q.first_key and p.last_key == q.last_key for q in persons):
                persons.append(p)
    emails = N.split_emails(g("match_email")) + N.split_emails(g("match_other_email"))
    phones = [N.parse_phone(p) for p in N.split_phones(g("match_phones"))]
    street = g("match_address")
    addr = N.address_key(street)
    address_key = f"{addr}|{N.zip_key(g('match_zip'))}" if addr else ""
    label = household or " & ".join(
        f"{p.first} {p.last}".strip() for p in persons) or g("company") or "(no name)"
    explicit = g("contact_key")
    key = explicit or "|".join([
        ",".join(sorted(f"{p.first_key}.{p.last_key}" for p in persons)),
        N.name_key(g("company")), ",".join(sorted(N.email_key(e) for e in emails)),
        ",".join(sorted(p.main for p in phones)), addr])
    return WorklistIdentity(key=key, label=label, persons=persons, company=g("company"),
                            emails=emails, phones=phones, address_key=address_key,
                            tp_id=g("tp_id"))
