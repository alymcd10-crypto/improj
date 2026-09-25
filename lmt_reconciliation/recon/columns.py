"""Detect which column plays which role.

Detection is only a starting point: the interface shows the result and lets
the user correct it before anything is compared.
"""
from __future__ import annotations

from .normalize import header_key

# ----------------------------------------------------------------- TP export
TP_SINGLE_ROLES = {
    "tp_id": ["contactid", "tpid", "tpcontactid", "topproducerid", "id", "recordid",
              "contactuuid", "uuid", "contactkey", "contactguid"],
    "first": ["firstname", "first", "fname", "givenname", "primaryfirstname"],
    "last": ["lastname", "last", "lname", "surname", "primarylastname"],
    "spouse_first": ["spousefirstname", "spousefirst", "secondaryfirstname", "partnerfirstname",
                     "spousename", "secondaryfirst", "cofirstname"],
    "spouse_last": ["spouselastname", "spouselast", "secondarylastname", "partnerlastname",
                    "secondarylast", "colastname"],
    "company": ["company", "companyname", "business", "businessname", "organization", "employer"],
    "display_name": ["fullname", "name", "displayname", "contactname", "household",
                     "householdname", "mailingname", "envelopename"],
    "street": ["address", "street", "streetaddress", "address1", "addressline1", "homeaddress",
               "mailingaddress", "homestreet", "mailingstreet", "streetaddress1"],
    "city": ["city", "homecity", "mailingcity"],
    "state": ["state", "province", "homestate", "mailingstate"],
    "zip": ["zip", "zipcode", "postalcode", "postal", "homezip", "mailingzip", "zippostalcode"],
    "modified": ["lastmodified", "modified", "datemodified", "updated", "lastupdated",
                 "modifieddate", "lastmodifieddate", "updatedat"],
    "birthday": ["birthday", "birthdate", "dob", "dateofbirth"],
}
TP_MULTI_ROLES = ("emails", "phones", "phone_exts", "categories", "notes")
ALL_TP_ROLES = list(TP_SINGLE_ROLES) + list(TP_MULTI_ROLES)

ROLE_LABELS = {
    "tp_id": "Top Producer contact ID", "first": "First name", "last": "Last name",
    "spouse_first": "Spouse / secondary first name", "spouse_last": "Spouse / secondary last name",
    "company": "Company / business name", "display_name": "Full or household name",
    "street": "Street address", "city": "City", "state": "State", "zip": "ZIP",
    "modified": "Last modified date", "birthday": "Birthday",
    "emails": "Email columns", "phones": "Phone columns", "phone_exts": "Phone extension columns",
    "categories": "Contact type / category columns", "notes": "Note columns",
}

_EMAIL_EXCLUDE = ("opt", "status", "type", "label", "bounce", "consent", "subscr", "valid", "pref")
_PHONE_WORDS = ("phone", "mobile", "cell", "fax", "tel", "pager")
_PHONE_EXCLUDE = ("type", "label", "opt", "status", "consent", "pref", "dnc")
_CATEGORY_WORDS = ("contacttype", "category", "categories", "types", "groups", "tags",
                   "contactgroup", "contactcategory")
_NOTE_WORDS = ("note", "comment", "background", "remarks")


def detect_tp_columns(headers: list[str]) -> dict:
    keys = {h: header_key(h) for h in headers}
    roles: dict = {r: "" for r in TP_SINGLE_ROLES}
    roles.update({r: [] for r in TP_MULTI_ROLES})
    used = set()
    for role, syns in TP_SINGLE_ROLES.items():
        for syn in syns:  # synonyms are in priority order
            hit = next((h for h in headers if keys[h] == syn and h not in used), None)
            if hit:
                roles[role] = hit
                used.add(hit)
                break
    for h in headers:
        k = keys[h]
        if h in used:
            continue
        if "email" in k and not any(x in k for x in _EMAIL_EXCLUDE):
            roles["emails"].append(h)
        elif any(w in k for w in _PHONE_WORDS) and ("ext" in k.replace("text", "")) and "next" not in k:
            roles["phone_exts"].append(h)
        elif any(w in k for w in _PHONE_WORDS) and not any(x in k for x in _PHONE_EXCLUDE):
            roles["phones"].append(h)
        elif k in {"type", "contacttype", "contacttypes"} or any(w in k for w in _CATEGORY_WORDS):
            roles["categories"].append(h)
        elif any(w in k for w in _NOTE_WORDS):
            roles["notes"].append(h)
    return roles


# ----------------------------------------------------------------- worklist
WORKLIST_ROLES = {
    "change_id": ["changeid"],
    "queue": ["queue"],
    "status": ["status", "executionstatus"],
    "field": ["field", "fieldname"],
    "action": ["action", "changetype"],
    "proposed": ["proposedaddition", "proposedvalue", "proposed", "addition"],
    "tp_id": ["tpid", "tpcontactid", "topproducerid", "contactid"],
    "first": ["firstname", "first", "primaryfirst", "primaryfirstname"],
    "last": ["lastname", "last", "primarylast", "primarylastname"],
    "spouse_first": ["spousefirst", "spousefirstname", "secondaryfirst", "secondaryfirstname"],
    "spouse_last": ["spouselast", "spouselastname", "secondarylast", "secondarylastname"],
    "household": ["household", "householdname", "contactname", "contact", "displayname",
                  "fullname", "name"],
    "company": ["company", "companyname", "business", "businessname"],
    "match_email": ["matchemail"],
    "match_other_email": ["matchotheremail", "matchemail2"],
    "match_phones": ["matchphones", "matchphone"],
    "match_address": ["matchaddress", "matchstreet"],
    "match_city": ["matchcity"],
    "match_zip": ["matchzip", "matchpostal"],
    "source_value": ["sourcevalue", "historicalvalue", "sourceevidence", "evidence",
                     "agentofficevalue", "aovalue", "mastervalue"],
    "source_ref": ["sourceref", "sourcereference", "sourcerow", "mastersheetrow", "sourcesheet",
                   "sourcelocation"],
    "reason": ["reason", "finding", "issue", "auditfinding"],
    "execution_rule": ["executionrule"],
    "identity_rule": ["identityrule"],
    "current_tp_value": ["currenttpvalue"],
    "actual_change": ["actualchange"],
    "verified_after": ["verifiedaftervalue", "verifiedafter"],
    "verified_at": ["verifiedatutc", "verifiedat"],
    "operator": ["operator"],
    "run_id": ["runid"],
    "result_note": ["resultnote"],
    "contact_key": ["contactkey", "audititemcontact", "sourcecontactid", "mastercontactid",
                    "contactgroup"],
}
REQUIRED_WORKLIST = ("change_id", "action", "proposed")


def detect_worklist_columns(headers: list[str]) -> dict:
    keys = {h: header_key(h) for h in headers}
    roles, used = {}, set()
    for role, syns in WORKLIST_ROLES.items():
        roles[role] = ""
        for syn in syns:
            hit = next((h for h in headers if keys[h] == syn and h not in used), None)
            if hit:
                roles[role] = hit
                used.add(hit)
                break
    return roles


KNOWN_HEADER_KEYS = {s for syns in TP_SINGLE_ROLES.values() for s in syns} | {
    s for syns in WORKLIST_ROLES.values() for s in syns}
