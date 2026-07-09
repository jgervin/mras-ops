"""Shared seeding for fleet registry tests (plan A). Mirrors test_camera_admin's
inline helpers; kept in one module because five test files need them."""
import uuid


async def org_loc_sys(pool, *, sys_name="Sys1", loc_name="Loc", org_name="Org"):
    org, loc, sid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await pool.execute(
        "INSERT INTO organizations (id,name,organization_type) VALUES ($1,$2,'advertiser')", org, org_name)
    await pool.execute(
        "INSERT INTO locations (id,name,location_type) VALUES ($1,$2,'store')", loc, loc_name)
    await pool.execute(
        "INSERT INTO systems (id,organization_id,location_id,name) VALUES ($1,$2,$3,$4)",
        sid, org, loc, sys_name)
    return org, loc, sid


async def root_location(pool, *, name, location_type="mall"):
    lid = uuid.uuid4()
    await pool.execute(
        "INSERT INTO locations (id,name,location_type) VALUES ($1,$2,$3::location_type)",
        lid, name, location_type)
    return lid


async def child_location(pool, parent, *, name, location_type="zone"):
    lid = uuid.uuid4()
    await pool.execute(
        "INSERT INTO locations (id,parent_location_id,name,location_type) VALUES ($1,$2,$3,$4::location_type)",
        lid, parent, name, location_type)
    return lid


async def screen_group(pool, sid, *, name="Grp1", loc=None):
    gid = uuid.uuid4()
    await pool.execute(
        "INSERT INTO screen_groups (id,system_id,location_id,name) VALUES ($1,$2,$3,$4)",
        gid, sid, loc, name)
    return gid


async def camera(pool, sid, *, name="Cam1", screen_id=None, group=None, status="active"):
    cid = uuid.uuid4()
    await pool.execute(
        "INSERT INTO cameras (id,system_id,name,screen_id,screen_group_id,status) "
        "VALUES ($1,$2,$3,$4,$5,$6::device_status)",
        cid, sid, name, screen_id, group, status)
    return cid


async def display(pool, sid, *, name="Kiosk1", screen_id, group=None, status="active"):
    did = uuid.uuid4()
    await pool.execute(
        "INSERT INTO displays (id,system_id,name,screen_id,screen_group_id,status) "
        "VALUES ($1,$2,$3,$4,$5,$6::device_status)",
        did, sid, name, screen_id, group, status)
    return did


async def unresolved(pool, *, screen_id="display-9", kind="display", seen_count=3):
    uid = uuid.uuid4()
    await pool.execute(
        "INSERT INTO unresolved_devices (id,screen_id,kind,seen_count) VALUES ($1,$2,$3,$4)",
        uid, screen_id, kind, seen_count)
    return uid
