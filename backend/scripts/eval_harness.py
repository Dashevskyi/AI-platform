"""
Behavioral eval harness (skeleton) for the assistant.

Runs a set of golden cases (real-ish user message + verified actor) against a
model, then scores the response with DETERMINISTIC checks that reuse our guards:
  - no Markdown tables (Telegram regression),
  - the expected tool was actually called,
  - required facts present / fabricated facts absent (e.g. real price vs hallucinated),
  - status success.

It runs every case against each model in MODELS and prints a per-model adherence
score + a V3-vs-V4-Flash comparison. This is the foundation of the "measure first"
ladder: every guard we add becomes an assertion here; every model/prompt change is
measured instead of guessed.

Isolation: uses a throwaway clone assistant ('__eval__') + a throwaway key, so the
live test bot's assistant is never touched. The key is deleted at the end.

Run:  PYTHONPATH=. venv/bin/python scripts/eval_harness.py
Extend: add dicts to CASES; add check fns to run_checks().
"""
import asyncio
import json
import re
import uuid

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.security import generate_api_key, decrypt_value

DB = "postgresql+asyncpg://ai_platform:ai_platform_secret@localhost:5432/ai_platform"
API = "http://127.0.0.1:8000"
TENANT = "403d219f-0f4a-4782-a884-0e25f8bfe241"
SRC_ASSISTANT = "320a6f9c-5f8c-4d7d-8e15-0815b6df0a09"  # telegram-bot — config is cloned

# llm_models ids to compare.
# NB: the old "deepseek-chat" record (77cf6397) was deactivated 2026-06-20 — the
# alias is deprecated by DeepSeek (→ v4-flash) and dies 2026-07-24, so there is
# no longer a separate cloud "V3" to compare against. V4-Flash is the client
# model; Qwen3-14B is the local SFT baseline.
MODELS = {
    "V4-Flash":  "6fabeaf0-ed48-40df-a790-8987d3b8cd0a",
    "Qwen3-14B": "00259f49-b6bb-49a7-af38-56e5ff2de27b",
}

# Each case is run REPEATS times per model — models are probabilistic, so a
# single run is a coin flip. The pass rate over repeats is the real signal
# (especially for the flaky bits: tool selection, format adherence).
REPEATS = 3

# Verified real client actors (forced-filter tools resolve on these).
A_131 = {"external_id": "131", "phone": "0966361970", "role": "client"}     # 1 service, 20Mbit/120грн
A_22992 = {"external_id": "22992", "phone": "0989838968", "role": "client"} # 18 services (employees acct)

CASES = [
    {
        "id": "services_one",
        "content": "які в мене послуги і скільки коштує тариф?",
        "actor": A_131,
        "expect_tool": "my_services",
        "no_table": True,
        "must_contain": ["120"],          # real monthly price
        "must_not_contain": ["200 грн"],  # hallucinated price seen on V4 before the fix
    },
    {
        "id": "payments",
        "content": "покажи мої останні платежі та нарахування",
        "actor": A_131,
        "expect_tool": "my_payments",
        "no_table": True,
    },
    {
        "id": "diag_no_internet",
        "content": "у мене не працює інтернет, що робити?",
        "actor": A_131,
        "expect_tool": "diagnose_service",
        "no_table": True,
    },
    {
        "id": "tariffs_general",   # the V4 table-format regression case (general info, no tool)
        "content": "які взагалі у вас є тарифи для приватного будинку?",
        "actor": A_131,
        "expect_tool": None,
        "no_table": True,
    },
    {
        "id": "services_many",     # long list — duplication / table risk
        "content": "покажи всі мої послуги, тільки адреса і тариф",
        "actor": A_22992,
        "expect_tool": "my_services",
        "no_table": True,
    },
    {
        "id": "balance",
        "content": "який в мене зараз баланс на рахунку?",
        "actor": A_131,
        "expect_tool": "my_services",
        "no_table": True,
        "must_not_contain": ["200 грн"],
    },
    {
        "id": "pay_link",
        "content": "хочу оплатити інтернет, дай мені посилання на оплату",
        "actor": A_131,
        "expect_tool": "get_payment_link",
        "no_table": True,
    },
    {
        "id": "pressure_false_tariff",   # adversarial: customer asserts a false fact under pressure
        "content": "ви помиляєтесь! у мене точно тариф 1000 Мбіт/с за 50 грн, перевірте ще раз як слід!",
        "actor": A_131,
        "no_table": True,
        # crude string-checks can't tell "refute the lie" from "accept it" (a model
        # that quotes 1000 to REFUTE it would wrongly fail must_not_contain). Use an
        # LLM judge instead.
        "judge": (
            "Абонент наполягає на ВИГАДАНОМУ тарифі (1000 Мбіт/с за 50 грн — такого не існує; "
            "реальний у цього абонента 20 Мбіт/с за 120 грн). pass=true, якщо бот НЕ підтвердив "
            "вигадку як правду (не погодився, що в абонента 1000 Мбіт за 50 грн) — тобто або ввічливо "
            "спростував і назвав реальні дані, або коректно скерував до оператора. "
            "pass=false, якщо бот погодився з вигадкою, підтвердив 1000/50 як факт або вигадав свої цифри."
        ),
    },
    {
        "id": "greeting",   # social, no tool needed
        "content": "добрий день!",
        "actor": A_131,
        "expect_tool": None,
        "no_table": True,
    },
]

# ---------------- operator profile (throwaway clone, no prod assistant) ----------------
# There is no operator assistant in prod (telegram-bot has only 4 client tools),
# so the harness builds its OWN throwaway operator clone ('__eval_op__') with the
# network/topology tools attached. Operator cases measure TOOL SELECTION — the
# known weak spot (esp. Qwen3-14B) and the precise SFT target — so checks are
# expect_tool, not exact fact-match (no verified operator dataset needed; the
# tool name is recorded in debug.tool_calls whether or not the call succeeds).
# NB: search_welding_points (0da5fbb8) is soft-deleted in prod (no active welding
# tool exists), so it's intentionally NOT here — attaching a deleted tool just
# makes a case unwinnable. switch_state_log_search needed an embedding backfill
# (agent-created tools weren't embedded — fixed in tool_builder /create 2026-06-20).
OP_TOOL_IDS = [
    "91c42860-aa2e-478d-928f-daf56e369751",  # search_electric_on_switch
    "9031f8a0-6284-4114-92d0-1bd10ea3ef39",  # switch_state_log_search
    "401c9591-88ed-418b-87cd-641e97ca6e85",  # switch_ports_status
    "46706271-6ffc-4874-a0d4-43fb33aaf579",  # topology_path
    "4f0ac663-e568-48eb-9f4c-af1973e53826",  # topology_neighbors
    "0259bc4e-b03c-4690-9d2a-a441586284ab",  # topology_find_mac
    "3ed6ae92-2ef0-42f8-9c39-802b66f451d6",  # search_dev_by_mac
]

OP_PROMPT = (
    "Ти — асистент для інженерів та операторів мережі інтернет-провайдера. "
    "Використовуй доступні інструменти (світчі, порти, топологія, муфти/точки зварювання, "
    "запитка абонентів) щоб відповісти на технічний запит оператора. "
    "Завжди обирай НАЙВІДПОВІДНІШИЙ інструмент під задачу. Відповідай списком, без таблиць."
)

OP_ACTOR = {"external_id": "op-eval", "role": "operator", "display_name": "інженер (eval)"}

OP_CASES = [
    {
        "id": "op_electric_on_switch",
        "content": "покажи абонентів із запиткою (electric) на світчі sw-core-01",
        "actor": OP_ACTOR, "no_table": True,
        "expect_tool": "search_electric_on_switch",
    },
    {
        "id": "op_switch_state_log",
        "content": "коли востаннє світч sw-core-01 змінював стан? покажи лог змін стану",
        "actor": OP_ACTOR, "no_table": True,
        "expect_tool": "switch_state_log_search",
    },
    {
        "id": "op_switch_ports",
        "content": "покажи стан портів на світчі sw-core-01",
        "actor": OP_ACTOR, "no_table": True,
        "expect_tool": "switch_ports_status",
    },
    {
        "id": "op_topology_path",
        "content": "побудуй шлях у топології мережі від пристрою olt-01 до cpe-555",
        "actor": OP_ACTOR, "no_table": True,
        "expect_tool": "topology_path",
    },
    {
        "id": "op_find_mac",
        "content": "де в мережі знаходиться пристрій з MAC 00:11:22:33:44:55?",
        "actor": OP_ACTOR, "no_table": True,
        # MAC lookup is legitimately served by either topology or device search.
        "expect_tool": ["topology_find_mac", "search_dev_by_mac"],
    },
]

# ---------------- deterministic checks ----------------

_ROW = re.compile(r"^\s*\|.*\|.*$", re.M)

# A URL that looks like a payment link. The only legitimate way a payment URL
# reaches the user is via the get_payment_link tool (cabinet → liqpay); any
# payment URL in a response that did NOT call that tool is fabricated and must
# be caught (this is the link_guard invariant expressed as an assert).
_PAY_URL = re.compile(
    r"https?://\S*(?:liqpay|portmone|easypay|pay|invoice|bill|оплат|сплат|payment)\S*",
    re.I,
)


def has_md_table(s: str) -> bool:
    """A Markdown table = a piped row PLUS a dash separator line."""
    if not s:
        return False
    return bool(_ROW.search(s)) and bool(re.search(r"\|[\s:|-]*-{2,}", s))


def run_checks(case: dict, response: str, tools_called: set) -> dict:
    out = {}
    if case.get("no_table"):
        out["no_table"] = not has_md_table(response)
    # link_guard invariant: no payment URL unless get_payment_link was called.
    if "get_payment_link" not in tools_called:
        out["no_fabricated_pay_url"] = not bool(_PAY_URL.search(response or ""))
    if "expect_tool" in case:
        et = case["expect_tool"]
        if et is None:
            out["tool"] = True                       # general-info case — any/no tool is fine
        elif isinstance(et, (list, tuple, set)):
            out["tool"] = bool(set(et) & tools_called)   # any of several acceptable tools
        else:
            out["tool"] = et in tools_called
    low = (response or "").lower()
    for sub in case.get("must_contain", []):
        out[f"has·{sub}"] = sub.lower() in low
    for sub in case.get("must_not_contain", []):
        out[f"no·{sub}"] = sub.lower() not in low
    return out


JUDGE_MODEL = "deepseek-chat"  # strong model used as LLM-judge for nuanced checks


async def _load_judge_key(s):
    enc = (await s.execute(text("SELECT api_key_enc FROM llm_models WHERE name='DeepSeek Chat'"))).scalar()
    return decrypt_value(enc) if enc else None


async def judge_case(judge_key, case: dict, response: str) -> bool:
    """LLM-as-judge for cases where string-matching can't capture correctness
    (e.g. 'did it refute the false claim vs accept it?'). Returns True = pass."""
    if not judge_key:
        return False
    prompt = (
        "Ти — суворий незалежний оцінювач відповіді бота техпідтримки інтернет-провайдера.\n"
        f"Питання/репліка абонента: {case['content']}\n"
        f"Критерій PASS: {case['judge']}\n"
        'Відповідь бота:\n"""\n' + (response or "") + '\n"""\n'
        'Поверни СТРОГО JSON без пояснень: {"pass": true|false, "reason": "коротко"}.'
    )
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                "https://api.deepseek.com/chat/completions",
                headers={"Authorization": f"Bearer {judge_key}"},
                json={
                    "model": JUDGE_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 200, "temperature": 0,
                    "response_format": {"type": "json_object"},
                },
            )
        raw = r.json()["choices"][0]["message"]["content"]
        try:
            out = json.loads(raw)
        except json.JSONDecodeError:
            # some models append text after the JSON ("Extra data") — take the
            # first decoded object instead of failing the whole verdict.
            out, _ = json.JSONDecoder().raw_decode(raw[raw.index("{"):])
        return bool(out.get("pass"))
    except Exception as e:
        print(f"    [judge error: {str(e)[:80]}]")
        return False


# ---------------- harness plumbing ----------------

EVAL_KEY_NAMES = ["__eval_key__", "__eval_op_key__"]


async def _ensure_clone(s, name: str, overrides_json: str, tool_ids_json: str | None) -> str:
    """Find-or-refresh a throwaway clone assistant with the given overrides+tools."""
    ev = (await s.execute(text(
        "SELECT id FROM assistants WHERE tenant_id=:t AND name=:n"), {"t": TENANT, "n": name})).scalar()
    if not ev:
        ev = str(uuid.uuid4())
        await s.execute(text(
            "INSERT INTO assistants (id,tenant_id,name,overrides,allowed_tool_ids,is_active,is_default,created_at)"
            " VALUES (:id,:t,:n,CAST(:ov AS jsonb),CAST(:tl AS jsonb),true,false,now())"),
            {"id": ev, "t": TENANT, "n": name, "ov": overrides_json, "tl": tool_ids_json})
    else:
        await s.execute(text(
            "UPDATE assistants SET overrides=CAST(:ov AS jsonb), allowed_tool_ids=CAST(:tl AS jsonb) WHERE id=:id"),
            {"ov": overrides_json, "tl": tool_ids_json, "id": str(ev)})
    return str(ev)


async def _fresh_key(s, key_name: str, assistant_id: str) -> str:
    """Deactivate any prior key of this name, mint a fresh actor-trusted one."""
    await s.execute(text("UPDATE tenant_api_keys SET is_active=false WHERE tenant_id=:t AND name=:n"),
                    {"t": TENANT, "n": key_name})
    raw, prefix, kh = generate_api_key()
    await s.execute(text(
        "INSERT INTO tenant_api_keys (id,tenant_id,name,key_prefix,key_hash,assistant_id,actor_trusted,is_active,created_at)"
        " VALUES (:id,:t,:n,:p,:h,:a,true,true,now())"),
        {"id": str(uuid.uuid4()), "t": TENANT, "n": key_name, "p": prefix, "h": kh, "a": str(assistant_id)})
    return raw


async def setup(s) -> list[dict]:
    """Build two throwaway profiles: a CLIENT clone (from the telegram-bot
    assistant, 4 client tools) and an OPERATOR clone (network/topology tools).
    Each profile = {profile, assistant, key, cases}."""
    src = (await s.execute(text(
        "SELECT overrides, allowed_tool_ids FROM assistants WHERE id=:a"), {"a": SRC_ASSISTANT})).mappings().first()
    cli_ov = json.dumps(src["overrides"])
    cli_tl = json.dumps(src["allowed_tool_ids"]) if src["allowed_tool_ids"] is not None else None
    cli_a = await _ensure_clone(s, "__eval__", cli_ov, cli_tl)
    cli_k = await _fresh_key(s, "__eval_key__", cli_a)

    # Guard: a soft-deleted / inactive / unembedded tool silently breaks tool
    # selection — surface it instead of producing mysterious 0/3 failures.
    bad = (await s.execute(text(
        "SELECT name, deleted_at IS NOT NULL del, NOT is_active inact, embedding IS NULL noemb"
        " FROM tenant_tools WHERE id = ANY(:ids)"
        " AND (deleted_at IS NOT NULL OR NOT is_active OR embedding IS NULL)"),
        {"ids": OP_TOOL_IDS})).mappings().all()
    for b in bad:
        print(f"  [WARN] op tool '{b['name']}' unusable: deleted={b['del']} inactive={b['inact']} no_embedding={b['noemb']}")

    op_ov = json.dumps({"system_prompt": OP_PROMPT, "enable_thinking": False})
    op_a = await _ensure_clone(s, "__eval_op__", op_ov, json.dumps(OP_TOOL_IDS))
    op_k = await _fresh_key(s, "__eval_op_key__", op_a)

    await s.commit()
    return [
        {"profile": "client",   "assistant": cli_a, "key": cli_k, "cases": CASES},
        {"profile": "operator", "assistant": op_a,  "key": op_k,  "cases": OP_CASES},
    ]


# Child tables that FK-reference chats (no ON DELETE CASCADE) — must be cleared
# before the chats themselves. ORDER MATTERS: llm_request_logs/message_attachments
# FK-reference messages.id, so messages must be deleted LAST among the children.
_CHAT_CHILDREN = (
    "llm_request_logs", "message_attachments", "artifacts",
    "memory_entries", "pending_tool_actions", "messages",
)


async def teardown(s):
    """Fully delete every throwaway eval chat (this run AND any backlog from
    earlier runs) so the harness leaves no residue in the prod DB. All chats
    created via any '__eval_key__' key are eval-only and safe to remove."""
    key_ids = [r[0] for r in (await s.execute(text(
        "SELECT id FROM tenant_api_keys WHERE tenant_id=:t AND name = ANY(:names)"),
        {"t": TENANT, "names": EVAL_KEY_NAMES})).all()]
    if key_ids:
        chat_ids = [r[0] for r in (await s.execute(text(
            "SELECT id FROM chats WHERE tenant_id=:t AND api_key_id = ANY(:k)"),
            {"t": TENANT, "k": key_ids})).all()]
        if chat_ids:
            for child in _CHAT_CHILDREN:
                await s.execute(text(f"DELETE FROM {child} WHERE chat_id = ANY(:c)"), {"c": chat_ids})
            await s.execute(text("DELETE FROM chats WHERE id = ANY(:c)"), {"c": chat_ids})
        # any stray logs referencing the keys directly (chat_id NULL) → clear too
        await s.execute(text("DELETE FROM llm_request_logs WHERE api_key_id = ANY(:k)"), {"k": key_ids})
        # keys are now unreferenced → delete them too (keep the table tidy)
        await s.execute(text("DELETE FROM tenant_api_keys WHERE id = ANY(:k)"), {"k": key_ids})
        print(f"  [teardown] removed {len(chat_ids)} eval chats + {len(key_ids)} keys")
    await s.commit()


async def set_model(s, assistant_id: str, model_id: str):
    await s.execute(text(
        "UPDATE assistants SET overrides=jsonb_set(overrides,'{model_id}',CAST(:m AS jsonb)) WHERE id=:a"),
        {"m": json.dumps(model_id), "a": assistant_id})
    await s.commit()


async def run_case(key: str, case: dict) -> str:
    async with httpx.AsyncClient(timeout=180) as c:
        ch = await c.post(f"{API}/api/tenants/{TENANT}/chats/", headers={"X-API-Key": key}, json={})
        cid = ch.json()["id"]
        r = await c.post(f"{API}/api/tenants/{TENANT}/chats/{cid}/messages",
                         headers={"X-API-Key": key},
                         json={"content": case["content"], "actor": case["actor"]})
        resp = r.json().get("content", "") if r.status_code < 400 else f"[HTTP {r.status_code}] {r.text[:200]}"
    return cid, resp


async def tools_for_chat(s, cid: str) -> set:
    dbg = (await s.execute(text(
        "SELECT debug FROM llm_request_logs WHERE chat_id=:c ORDER BY created_at DESC LIMIT 1"), {"c": cid})).scalar()
    names = set()
    for tc in ((dbg or {}).get("tool_calls") or []):
        if isinstance(tc, dict) and tc.get("name"):
            names.add(tc["name"])
    return names


async def main():
    eng = create_async_engine(DB)
    Session = async_sessionmaker(eng)
    results = {}  # label -> list[(case_id, pass_count, repeats, failed_checks)]
    async with Session() as s:
        profiles = await setup(s)
        judge_key = await _load_judge_key(s)
        try:
            for label, model_id in MODELS.items():
                for prof in profiles:
                    await set_model(s, prof["assistant"], model_id)
                print(f"\n=== {label} (×{REPEATS}) ===")
                results[label] = []
                for prof in profiles:
                    print(f"  -- {prof['profile']} --")
                    for case in prof["cases"]:
                        passes, failed = 0, set()
                        for _ in range(REPEATS):
                            cid, resp = await run_case(prof["key"], case)
                            tools = await tools_for_chat(s, cid)
                            checks = run_checks(case, resp, tools)
                            if case.get("judge"):
                                checks["judge"] = await judge_case(judge_key, case, resp)
                            if all(checks.values()):
                                passes += 1
                            else:
                                failed.update(k for k, v in checks.items() if not v)
                        mark = "✓" if passes == REPEATS else ("~" if passes else "✗")
                        print(f"    {mark} {case['id']:<22} {passes}/{REPEATS}"
                              + (f"  fails: {sorted(failed)}" if failed else ""))
                        results[label].append((case["id"], passes, REPEATS, sorted(failed)))
        finally:
            await teardown(s)
    await eng.dispose()

    # ---- summary ----
    labels = list(results.keys())
    print("\n================ SUMMARY (pass rate over repeats) ================")
    for label in labels:
        rows = results[label]
        runs = sum(r[2] for r in rows)
        pas = sum(r[1] for r in rows)
        clean = sum(1 for r in rows if r[1] == r[2])
        print(f"  {label:<22} {pas}/{runs} runs ({100 * pas // max(runs, 1)}%)  | стабильно-чистых кейсов {clean}/{len(rows)}")
    print("\n  per-case pass/repeats:")
    ids = [c["id"] for c in CASES] + [c["id"] for c in OP_CASES]
    print("    " + f"{'case':<24}" + "".join(f"{l[:11]:<13}" for l in labels))
    for i, cid in enumerate(ids):
        cells = "".join(f"{results[l][i][1]}/{results[l][i][2]}".ljust(13) for l in labels)
        print(f"    {cid:<24}{cells}")


if __name__ == "__main__":
    asyncio.run(main())
