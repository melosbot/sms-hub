"""Contact alias routes."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.app import auth
from core.infra import db
from core.sms import phone as phone_module

router = APIRouter(dependencies=[Depends(auth.require_auth)])


class ContactBody(BaseModel):
    alias: str


@router.get("/api/contacts")
async def list_contacts():
    async with db.db().execute(
        "SELECT phone, alias, updated_at FROM contacts ORDER BY alias COLLATE NOCASE, phone"
    ) as cur:
        return {"contacts": [dict(r) for r in await cur.fetchall()]}


@router.put("/api/contacts/{phone}")
async def save_contact(phone: str, body: ContactBody):
    phone = phone_module.canonicalize(phone)
    alias = body.alias.strip()
    if not phone:
        raise HTTPException(status_code=400, detail="号码不能为空")
    if not alias:
        await db.db().execute("DELETE FROM contacts WHERE phone=?", (phone,))
        await db.db().commit()
        return {"ok": True, "deleted": True}
    await db.db().execute(
        "INSERT INTO contacts(phone, alias, updated_at)"
        " VALUES(?,?,datetime('now','localtime'))"
        " ON CONFLICT(phone) DO UPDATE SET alias=excluded.alias,"
        " updated_at=datetime('now','localtime')",
        (phone, alias),
    )
    await db.db().commit()
    return {"ok": True, "phone": phone, "alias": alias}


@router.delete("/api/contacts/{phone}")
async def delete_contact(phone: str):
    await db.db().execute(
        "DELETE FROM contacts WHERE phone=?", (phone_module.canonicalize(phone),)
    )
    await db.db().commit()
    return {"ok": True}
