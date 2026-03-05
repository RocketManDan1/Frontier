import json
import sqlite3

import pytest

import mission_service
from org_service import ensure_org_for_user


def _ensure_user(conn: sqlite3.Connection, username: str) -> None:
	conn.execute(
		"INSERT OR IGNORE INTO users (username, password_hash, is_admin, created_at) VALUES (?,?,0,?)",
		(username, "test_hash", 0.0),
	)
	conn.commit()


def _ensure_location(conn: sqlite3.Connection, location_id: str, name: str) -> None:
	conn.execute(
		"""INSERT OR IGNORE INTO locations (id, name, parent_id, is_group, sort_order, x, y)
		   VALUES (?, ?, NULL, 0, 0, 0, 0)""",
		(location_id, name),
	)
	conn.commit()


def _insert_mission(
	conn: sqlite3.Connection,
	*,
	mission_id: str,
	tier: str,
	destination_id: str,
	destination_name: str,
	status: str = "available",
	org_id: str | None = None,
	accepted_at: float | None = None,
	expires_at: float | None = None,
	power_started_at: float | None = None,
) -> None:
	payout = mission_service.PAYOUTS[tier]
	now = 946684800.0
	conn.execute(
		"""INSERT INTO missions
		   (id, tier, title, description, destination_id, destination_name,
			status, payout_total, payout_upfront, payout_completion,
			org_id, accepted_at, expires_at, delivered_at,
			power_started_at, power_required_s, completed_at,
			created_at, available_expires_at)
		   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
		(
			mission_id,
			tier,
			f"Test {tier} mission",
			"test",
			destination_id,
			destination_name,
			status,
			payout["total"],
			payout["upfront"],
			payout["completion"],
			org_id,
			accepted_at,
			expires_at,
			None,
			power_started_at,
			90 * 86400 if tier == "hard" else 0,
			None,
			now,
			now + (5 * 365.25 * 86400),
		),
	)
	conn.commit()


def test_accept_credits_once_and_blocks_second_active(seeded_db):
	_ensure_user(seeded_db, "mission_user")
	_ensure_location(seeded_db, "LEO", "Low Earth Orbit")
	org_id = ensure_org_for_user(seeded_db, "mission_user")

	available = mission_service.get_available_missions(seeded_db)
	assert len(available) >= 2
	first = available[0]
	second = available[1]

	bal_before = float(
		seeded_db.execute("SELECT balance_usd FROM organizations WHERE id=?", (org_id,)).fetchone()["balance_usd"]
	)

	accepted = mission_service.accept_mission(seeded_db, first["id"], org_id)
	assert accepted["status"] == "accepted"

	bal_after = float(
		seeded_db.execute("SELECT balance_usd FROM organizations WHERE id=?", (org_id,)).fetchone()["balance_usd"]
	)
	assert bal_after == pytest.approx(bal_before + float(first["payout_upfront"]))

	with pytest.raises(ValueError):
		mission_service.accept_mission(seeded_db, second["id"], org_id)


def test_settle_expired_active_fails_and_claws_back(seeded_db):
	_ensure_user(seeded_db, "expiry_user")
	_ensure_location(seeded_db, "LEO", "Low Earth Orbit")
	org_id = ensure_org_for_user(seeded_db, "expiry_user")
	available = mission_service.get_available_missions(seeded_db)
	mission_id = available[0]["id"]
	upfront = float(available[0]["payout_upfront"])

	bal_before = float(
		seeded_db.execute("SELECT balance_usd FROM organizations WHERE id=?", (org_id,)).fetchone()["balance_usd"]
	)
	mission_service.accept_mission(seeded_db, mission_id, org_id)

	stack_key = mission_service.mission_module_stack_key(mission_id)
	stack = seeded_db.execute(
		"SELECT 1 FROM location_inventory_stacks WHERE stack_key=? LIMIT 1",
		(stack_key,),
	).fetchone()
	assert stack is not None

	bal_after_accept = float(
		seeded_db.execute("SELECT balance_usd FROM organizations WHERE id=?", (org_id,)).fetchone()["balance_usd"]
	)
	assert bal_after_accept == pytest.approx(bal_before + upfront)

	seeded_db.execute("UPDATE missions SET expires_at=? WHERE id=?", (0.0, mission_id))
	seeded_db.commit()
	mission_service.settle_missions(seeded_db)

	row = seeded_db.execute("SELECT status FROM missions WHERE id=?", (mission_id,)).fetchone()
	assert row["status"] == "failed"

	stack = seeded_db.execute(
		"SELECT 1 FROM location_inventory_stacks WHERE stack_key=? LIMIT 1",
		(stack_key,),
	).fetchone()
	assert stack is None

	bal_after_settle = float(
		seeded_db.execute("SELECT balance_usd FROM organizations WHERE id=?", (org_id,)).fetchone()["balance_usd"]
	)
	assert bal_after_settle == pytest.approx(bal_before)


def test_complete_succeeds_when_module_in_docked_ship(seeded_db):
	_ensure_user(seeded_db, "ship_complete_user")
	org_id = ensure_org_for_user(seeded_db, "ship_complete_user")
	_insert_mission(
		seeded_db,
		mission_id="msn_ship_complete",
		tier="easy",
		destination_id="LMO",
		destination_name="Low Mars Orbit",
		status="accepted",
		org_id=org_id,
		accepted_at=946684800.0,
		expires_at=946684800.0 + (15 * 365.25 * 86400),
	)

	seeded_db.execute(
		"""INSERT INTO ships (id, name, shape, color, size_px, notes_json, location_id, parts_json)
		   VALUES (?, ?, 'triangle', '#ffffff', 12, '[]', ?, ?)""",
		(
			"ship_mission_1",
			"Mission Carrier",
			"LMO",
			json.dumps([{"item_id": "mission_materials_module", "_mission_id": "msn_ship_complete"}]),
		),
	)
	seeded_db.commit()

	bal_before = float(
		seeded_db.execute("SELECT balance_usd FROM organizations WHERE id=?", (org_id,)).fetchone()["balance_usd"]
	)
	result = mission_service.complete_mission(seeded_db, "msn_ship_complete", org_id)
	assert result["status"] == "completed"

	bal_after = float(
		seeded_db.execute("SELECT balance_usd FROM organizations WHERE id=?", (org_id,)).fetchone()["balance_usd"]
	)
	assert bal_after == pytest.approx(bal_before + mission_service.PAYOUTS["easy"]["completion"])


def test_hard_power_timer_resets_when_power_drops(seeded_db, monkeypatch):
	_ensure_user(seeded_db, "hard_power_user")
	_ensure_location(seeded_db, "MARS_HELLAS", "Hellas Planitia")
	org_id = ensure_org_for_user(seeded_db, "hard_power_user")
	_insert_mission(
		seeded_db,
		mission_id="msn_hard_reset",
		tier="hard",
		destination_id="MARS_HELLAS",
		destination_name="Hellas Planitia",
		status="powered",
		org_id=org_id,
		accepted_at=1000.0,
		expires_at=1_000_000_000.0,
		power_started_at=10_000.0,
	)
	mission_service.mint_mission_module(seeded_db, "msn_hard_reset", "MARS_HELLAS", org_id)

	monkeypatch.setattr(mission_service, "game_now_s", lambda: 12_000.0)
	monkeypatch.setattr(mission_service, "_check_facility_power", lambda _conn, _loc: False)

	with pytest.raises(ValueError, match="timer reset"):
		mission_service.complete_mission(seeded_db, "msn_hard_reset", org_id)

	row = seeded_db.execute(
		"SELECT power_started_at, power_reset_count, last_power_reset_at FROM missions WHERE id=?",
		("msn_hard_reset",),
	).fetchone()
	assert float(row["power_started_at"]) == pytest.approx(12_000.0)
	assert int(row["power_reset_count"]) == 1
	assert float(row["last_power_reset_at"]) == pytest.approx(12_000.0)


def test_db_guardrails_unique_active_and_payout_invariant(db_conn):
	_ensure_user(db_conn, "guardrail_user")
	org_id = ensure_org_for_user(db_conn, "guardrail_user")

	_insert_mission(
		db_conn,
		mission_id="msn_guardrail_a",
		tier="easy",
		destination_id="LMO",
		destination_name="Low Mars Orbit",
		status="accepted",
		org_id=org_id,
		accepted_at=1.0,
		expires_at=2.0,
	)

	with pytest.raises(sqlite3.IntegrityError):
		_insert_mission(
			db_conn,
			mission_id="msn_guardrail_b",
			tier="medium",
			destination_id="MARS_HELLAS",
			destination_name="Hellas Planitia",
			status="delivered",
			org_id=org_id,
			accepted_at=1.0,
			expires_at=2.0,
		)

	with pytest.raises(sqlite3.IntegrityError):
		db_conn.execute(
			"""INSERT INTO missions
			   (id, tier, title, description, destination_id, destination_name,
				status, payout_total, payout_upfront, payout_completion,
				created_at, available_expires_at)
			   VALUES (?, 'easy', 'bad payout', '', 'LMO', 'Low Mars Orbit',
					   'available', 123, 100, 50, 0, 1)""",
			("msn_bad_payout",),
		)
