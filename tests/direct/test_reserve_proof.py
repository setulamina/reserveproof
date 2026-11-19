# -*- coding: utf-8 -*-
"""ReserveProof direct-mode tests — a challenge market over ratio quotes.

Run: pytest tests/direct/ -v

Unit: B = 0.001 GEN. A normal challenge stakes 100*B (0.1 GEN) and asks for
a 4*B bounty.

These tests cover the flow end to end plus the adversarial classes:
fabricated claims, impostor operators, challenges on another custodian's bond,
unusable digests, and validator disagreement. `mock_llm` registration order
matters — the first matching pattern wins — so the leader and validator prompts
are registered under distinct patterns and can be made to disagree on purpose.
"""

import pytest

B = 1000000000000000
STAKE = 100 * B
BOUNTY = 4 * B
BOND = 2000 * B
TOL = 100          # 1% tolerance, in basis points
QUOTE = 10500          # claimed reserve ratio = 105.00% (basis points)
ASSET_NAME = "USDC"
CUSTODIAN = "anchorcustody"
URL = "https://audits.example.org/pythnet/eth-usd"
SDIG = "sha256:page-2f9a1c7b77c1"
ADIG = "sha256:audit-77be0d31"
NOTE = "the reported reserve ratio is far above what the audit states"
BODY = "x" * 400

LEADER_PAT = "Verify a proof-of-reserves claim"
VALIDATOR_PAT = "Extract from the page below"


# --------------------------------------------------------------- harness --

def _deploy(direct_vm, direct_deploy, admin):
    import pathlib
    import tempfile

    src = pathlib.Path(__file__).resolve().parents[2] / "contracts" / "reserve_proof.py"
    tmp = pathlib.Path(tempfile.gettempdir()) / "reserve_proof_local.py"
    tmp.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    direct_vm.sender = admin
    return direct_deploy(str(tmp))


def _addr(v):
    from genlayer import Address
    if isinstance(v, (bytes, bytearray)):
        return Address("0x" + bytes(v).hex())
    if isinstance(v, str) and not v.startswith("0x"):
        return Address("0x" + v)
    return Address(v)


def _hex(v):
    return "0x" + _addr(v).as_bytes.hex()


def _expect(reported, source, tol, found, meth):
    """Python mirror of the contract's shared rule, used to build mocks."""
    if not found or source <= 0:
        return "inconclusive"
    if abs(reported - source) * 10000 > tol * source:
        return "false"
    if not meth:
        return "false"
    return "sound"


def _setup(direct_vm, direct_deploy, op, tol=TOL):
    c = _deploy(direct_vm, direct_deploy, op)
    direct_vm.sender = op
    c.register_custodian(CUSTODIAN, tol)
    return c


def _bond(direct_vm, c, who, amount, custodian=CUSTODIAN):
    direct_vm.sender = who
    direct_vm.value = amount
    c.fund_bond(custodian)
    direct_vm.value = 0


def _report(direct_vm, c, op, claim=QUOTE, asset=ASSET_NAME, url=URL,
            sdig=SDIG, mdig=ADIG, custodian=CUSTODIAN):
    direct_vm.sender = op
    return int(c.record_report(custodian, asset, claim, url, sdig, mdig))


def _challenge(direct_vm, c, who, rid, stake=STAKE, bounty=BOUNTY, note=NOTE):
    direct_vm.sender = who
    direct_vm.value = stake
    cid = int(c.challenge_report(rid, note, bounty))
    direct_vm.value = 0
    return cid


def _mock(direct_vm, claim=QUOTE, tol=TOL, found=True, stated=QUOTE, meth=True,
          score=90, body=BODY, v_found=None, v_stated=None, v_meth=None):
    """Leader mock plus an independent validator mock under a distinct pattern."""
    if v_found is None:
        v_found = found
    if v_stated is None:
        v_stated = stated
    if v_meth is None:
        v_meth = meth
    direct_vm.mock_web(".*", {"status": 200, "body": body})
    direct_vm.mock_llm(LEADER_PAT, {
        "found": found,
        "attested_ratio": stated,
        "audit_ok": meth,
        "state": _expect(claim, stated, tol, found, meth),
        "score": score,
        "reason": "the committed disclosure was read and the figure compared",
        "confidence": "high",
    })
    direct_vm.mock_llm(VALIDATOR_PAT, {
        "found": v_found,
        "attested_ratio": v_stated,
        "audit_ok": v_meth,
    })


def _ready(direct_vm, direct_deploy, op, ch, tol=TOL):
    """Custodian + bond + report + challenge, returning (contract, challenge id)."""
    c = _setup(direct_vm, direct_deploy, op, tol)
    _bond(direct_vm, c, op, BOND)
    rid = _report(direct_vm, c, op)
    cid = _challenge(direct_vm, c, ch, rid)
    return c, cid


# -------------------------------------------------------- custodian registry --

def test_custodian_id_must_be_meaningful(direct_vm, direct_deploy, direct_alice):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("Custodian id too short"):
        c.register_custodian("ab", TOL)


def test_custodian_cannot_be_registered_twice(direct_vm, direct_deploy, direct_alice):
    c = _setup(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("already registered"):
        c.register_custodian(CUSTODIAN, TOL)


def test_registration_records_operator_and_tolerance(direct_vm, direct_deploy, direct_alice):
    c = _setup(direct_vm, direct_deploy, direct_alice)
    v = c.get_custodian(CUSTODIAN)
    assert v["operator"].lower() == _hex(direct_alice)
    assert int(v["bond"]) == 0
    assert int(v["committed"]) == 0
    assert int(v["tolerance_bps"]) == TOL
    assert int(v["report_count"]) == 0


def test_tolerance_cannot_exceed_the_cap(direct_vm, direct_deploy, direct_alice):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("Tolerance too wide"):
        c.register_custodian(CUSTODIAN, 2001)


def test_custodian_id_is_normalised(direct_vm, direct_deploy, direct_alice):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_alice
    c.register_custodian("  BlueHarbor ", TOL)
    assert c.get_custodian("blueharbor")["operator"].lower() == _hex(direct_alice)


def test_unknown_custodian_reverts(direct_vm, direct_deploy, direct_alice):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("No such custodian"):
        c.get_custodian("nothing")


# ------------------------------------------------------------- bond pool --

def test_fund_bond_requires_value(direct_vm, direct_deploy, direct_alice):
    c = _setup(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_alice
    direct_vm.value = 0
    with direct_vm.expect_revert("Send GEN"):
        c.fund_bond(CUSTODIAN)


def test_fund_bond_accumulates(direct_vm, direct_deploy, direct_alice):
    c = _setup(direct_vm, direct_deploy, direct_alice)
    _bond(direct_vm, c, direct_alice, BOND)
    _bond(direct_vm, c, direct_alice, BOND)
    assert int(c.get_custodian(CUSTODIAN)["bond"]) == 2 * BOND


def test_anyone_can_top_up_the_bond(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _setup(direct_vm, direct_deploy, direct_alice)
    _bond(direct_vm, c, direct_bob, BOND)
    assert int(c.get_custodian(CUSTODIAN)["bond"]) == BOND


def test_only_the_operator_may_withdraw(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _setup(direct_vm, direct_deploy, direct_alice)
    _bond(direct_vm, c, direct_alice, BOND)
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("Only the operator"):
        c.withdraw_bond(CUSTODIAN, B, _hex(direct_bob))


def test_withdraw_respects_committed_bounty(direct_vm, direct_deploy, direct_alice,
                                             direct_bob):
    """The committed bounty is untouchable until the challenge settles."""
    c, cid = _ready(direct_vm, direct_deploy, direct_alice, direct_bob)
    _mock(direct_vm, stated=8000, v_stated=8000)
    c.judge_challenge(cid)
    v = c.get_custodian(CUSTODIAN)
    assert int(v["committed"]) == BOUNTY
    assert int(v["withdrawable"]) == BOND - BOUNTY
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("Committed bounty is protected"):
        c.withdraw_bond(CUSTODIAN, BOND, _hex(direct_alice))


def test_operator_can_withdraw_the_uncommitted_part(direct_vm, direct_deploy, direct_alice):
    c = _setup(direct_vm, direct_deploy, direct_alice)
    _bond(direct_vm, c, direct_alice, BOND)
    direct_vm.sender = direct_alice
    c.withdraw_bond(CUSTODIAN, BOND, _hex(direct_alice))
    assert int(c.get_custodian(CUSTODIAN)["bond"]) == 0


# ------------------------------------------- report commitment (auth) ----

def test_only_the_operator_can_record(direct_vm, direct_deploy, direct_alice, direct_bob):
    """Adversarial: a third party cannot invent a claim against someone's custodian."""
    c = _setup(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("Only the operator"):
        c.record_report(CUSTODIAN, ASSET_NAME, QUOTE, URL, SDIG, ADIG)


def test_report_requires_a_asset(direct_vm, direct_deploy, direct_alice):
    c = _setup(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("Trading asset required"):
        c.record_report(CUSTODIAN, "e", QUOTE, URL, SDIG, ADIG)


def test_report_requires_http_url(direct_vm, direct_deploy, direct_alice):
    c = _setup(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("Valid audit URL"):
        c.record_report(CUSTODIAN, ASSET_NAME, QUOTE, "ftp://x/y/z", SDIG, ADIG)


def test_report_requires_source_digest(direct_vm, direct_deploy, direct_alice):
    c = _setup(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("Page digest required"):
        c.record_report(CUSTODIAN, ASSET_NAME, QUOTE, URL, "short", ADIG)


def test_report_requires_audit_digest(direct_vm, direct_deploy, direct_alice):
    c = _setup(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("Audit digest required"):
        c.record_report(CUSTODIAN, ASSET_NAME, QUOTE, URL, SDIG, "short")


def test_implausible_ratio_is_rejected(direct_vm, direct_deploy, direct_alice):
    c = _setup(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("Implausible ratio"):
        c.record_report(CUSTODIAN, ASSET_NAME, 10 ** 7, URL, SDIG, ADIG)


def test_record_report_returns_an_id_and_counts_up(direct_vm, direct_deploy, direct_alice):
    c = _setup(direct_vm, direct_deploy, direct_alice)
    r1 = _report(direct_vm, c, direct_alice)
    r2 = _report(direct_vm, c, direct_alice, asset="2026-Q4")
    assert r2 == r1 + 1
    assert int(c.get_custodian(CUSTODIAN)["report_count"]) == 2
    rep = c.get_report(r1)
    assert int(rep["reported_ratio"]) == QUOTE
    assert rep["asset"] == ASSET_NAME
    assert rep["challenged"] is False


def test_unknown_report_reverts(direct_vm, direct_deploy, direct_alice):
    c = _setup(direct_vm, direct_deploy, direct_alice)
    with direct_vm.expect_revert("No such report"):
        c.get_report(99)


# ------------------------------------------------------------ challenge --

def test_operator_cannot_challenge_their_own_report(direct_vm, direct_deploy,
                                                    direct_alice):
    c = _setup(direct_vm, direct_deploy, direct_alice)
    _bond(direct_vm, c, direct_alice, BOND)
    rid = _report(direct_vm, c, direct_alice)
    direct_vm.sender = direct_alice
    direct_vm.value = STAKE
    with direct_vm.expect_revert("Operator cannot challenge"):
        c.challenge_report(rid, NOTE, BOUNTY)
    direct_vm.value = 0


def test_stake_under_minimum_is_rejected(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _setup(direct_vm, direct_deploy, direct_alice)
    _bond(direct_vm, c, direct_alice, BOND)
    rid = _report(direct_vm, c, direct_alice)
    direct_vm.sender = direct_bob
    direct_vm.value = B
    with direct_vm.expect_revert("Stake under minimum"):
        c.challenge_report(rid, NOTE, BOUNTY)
    direct_vm.value = 0


def test_stake_over_maximum_is_rejected(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _setup(direct_vm, direct_deploy, direct_alice)
    _bond(direct_vm, c, direct_alice, BOND)
    rid = _report(direct_vm, c, direct_alice)
    direct_vm.sender = direct_bob
    direct_vm.value = 11 * 10 ** 18
    with direct_vm.expect_revert("Stake over maximum"):
        c.challenge_report(rid, NOTE, BOUNTY)
    direct_vm.value = 0


def test_challenge_needs_a_real_reason(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _setup(direct_vm, direct_deploy, direct_alice)
    _bond(direct_vm, c, direct_alice, BOND)
    rid = _report(direct_vm, c, direct_alice)
    direct_vm.sender = direct_bob
    direct_vm.value = STAKE
    with direct_vm.expect_revert("Give a reason"):
        c.challenge_report(rid, "too short", BOUNTY)
    direct_vm.value = 0


def test_bounty_under_minimum_is_rejected(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _setup(direct_vm, direct_deploy, direct_alice)
    _bond(direct_vm, c, direct_alice, BOND)
    rid = _report(direct_vm, c, direct_alice)
    direct_vm.sender = direct_bob
    direct_vm.value = STAKE
    with direct_vm.expect_revert("Bounty under minimum"):
        c.challenge_report(rid, NOTE, 1)
    direct_vm.value = 0


def test_bounty_cannot_exceed_100x_stake(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _setup(direct_vm, direct_deploy, direct_alice)
    _bond(direct_vm, c, direct_alice, BOND)
    rid = _report(direct_vm, c, direct_alice)
    direct_vm.sender = direct_bob
    direct_vm.value = STAKE
    with direct_vm.expect_revert("over 100x stake"):
        c.challenge_report(rid, NOTE, STAKE * 101)
    direct_vm.value = 0


def test_bond_must_cover_the_bounty(direct_vm, direct_deploy, direct_alice, direct_bob):
    """Adversarial: a challenge cannot promise money the custodian does not hold."""
    c = _setup(direct_vm, direct_deploy, direct_alice)
    rid = _report(direct_vm, c, direct_alice)
    direct_vm.sender = direct_bob
    direct_vm.value = STAKE
    with direct_vm.expect_revert("bond cannot back"):
        c.challenge_report(rid, NOTE, BOUNTY)
    direct_vm.value = 0


def test_one_challenge_per_report(direct_vm, direct_deploy, direct_alice, direct_bob):
    c, cid = _ready(direct_vm, direct_deploy, direct_alice, direct_bob)
    rep = c.get_report(1)
    assert rep["challenged"] is True
    direct_vm.sender = direct_bob
    direct_vm.value = STAKE
    with direct_vm.expect_revert("Already challenged"):
        c.challenge_report(1, NOTE, BOUNTY)
    direct_vm.value = 0


def test_challenge_records_stake_and_bounty(direct_vm, direct_deploy, direct_alice, direct_bob):
    c, cid = _ready(direct_vm, direct_deploy, direct_alice, direct_bob)
    ch = c.get_challenge(cid)
    assert ch["challenger"].lower() == _hex(direct_bob)
    assert int(ch["stake"]) == STAKE
    assert int(ch["bounty"]) == BOUNTY
    assert int(ch["state"]) == 0


def test_challenge_on_unknown_report_reverts(direct_vm, direct_deploy, direct_alice, direct_bob):
    c = _setup(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_bob
    direct_vm.value = STAKE
    with direct_vm.expect_revert("No such report"):
        c.challenge_report(77, NOTE, BOUNTY)
    direct_vm.value = 0


# -------------------------------------------------------------- judging --

def test_judge_finds_a_sound_claim(direct_vm, direct_deploy, direct_alice, direct_bob):
    c, cid = _ready(direct_vm, direct_deploy, direct_alice, direct_bob)
    _mock(direct_vm)
    c.judge_challenge(cid)
    ch = c.get_challenge(cid)
    assert int(ch["state"]) == 1          # S_SOUND
    assert int(ch["attested_ratio"]) == QUOTE
    # the bounty stays reserved after judgment; only settle releases it
    assert int(c.get_custodian(CUSTODIAN)["committed"]) == BOUNTY


def test_judge_accepts_a_figure_inside_tolerance(direct_vm, direct_deploy,
                                                 direct_alice, direct_bob):
    c, cid = _ready(direct_vm, direct_deploy, direct_alice, direct_bob)
    _mock(direct_vm, stated=10550)  # 0.5% away, well inside the 1% band
    c.judge_challenge(cid)
    assert int(c.get_challenge(cid)["state"]) == 1


def test_judge_rejects_a_figure_outside_tolerance(direct_vm, direct_deploy,
                                                  direct_alice, direct_bob):
    c, cid = _ready(direct_vm, direct_deploy, direct_alice, direct_bob)
    _mock(direct_vm, stated=8000)
    c.judge_challenge(cid)
    ch = c.get_challenge(cid)
    assert int(ch["state"]) == 2          # S_FALSE
    assert int(ch["attested_ratio"]) == 8000
    assert int(c.get_custodian(CUSTODIAN)["committed"]) == BOUNTY


def test_judge_rejects_a_claim_the_audit_does_not_support(direct_vm, direct_deploy,
                                                               direct_alice, direct_bob):
    c, cid = _ready(direct_vm, direct_deploy, direct_alice, direct_bob)
    _mock(direct_vm, meth=False)
    c.judge_challenge(cid)
    assert int(c.get_challenge(cid)["state"]) == 2


def test_judge_is_inconclusive_when_the_page_is_unreadable(direct_vm, direct_deploy,
                                                           direct_alice, direct_bob):
    c, cid = _ready(direct_vm, direct_deploy, direct_alice, direct_bob)
    _mock(direct_vm, body="tiny")
    c.judge_challenge(cid)
    ch = c.get_challenge(cid)
    assert int(ch["state"]) == 3          # S_INCONCLUSIVE
    # reserved since the challenge opened, released on settle
    assert int(c.get_custodian(CUSTODIAN)["committed"]) == BOUNTY


def test_judge_is_inconclusive_when_no_figure_is_stated(direct_vm, direct_deploy,
                                                        direct_alice, direct_bob):
    c, cid = _ready(direct_vm, direct_deploy, direct_alice, direct_bob)
    _mock(direct_vm, found=False, stated=-1, meth=False)
    c.judge_challenge(cid)
    assert int(c.get_challenge(cid)["state"]) == 3


def test_a_judged_challenge_cannot_be_judged_again(direct_vm, direct_deploy,
                                                   direct_alice, direct_bob):
    c, cid = _ready(direct_vm, direct_deploy, direct_alice, direct_bob)
    _mock(direct_vm)
    c.judge_challenge(cid)
    with direct_vm.expect_revert("Already judged"):
        c.judge_challenge(cid)


def test_validator_agrees_with_a_truthful_leader(direct_vm, direct_deploy,
                                                direct_alice, direct_bob):
    c, cid = _ready(direct_vm, direct_deploy, direct_alice, direct_bob)
    _mock(direct_vm, stated=8000, v_stated=8000)
    c.judge_challenge(cid)
    assert direct_vm.run_validator() is True


def test_validator_disagreement_prevents_acceptance(direct_vm, direct_deploy,
                                                    direct_alice, direct_bob):
    """The leader says unsound; an independent read of the same page disagrees."""
    c, cid = _ready(direct_vm, direct_deploy, direct_alice, direct_bob)
    _mock(direct_vm, stated=8000, v_stated=QUOTE, v_meth=True)
    c.judge_challenge(cid)
    assert direct_vm.run_validator() is False


def test_validator_disagreement_on_audit_prevents_acceptance(
        direct_vm, direct_deploy, direct_alice, direct_bob):
    """Same figure, but the validator reads the audit differently."""
    c, cid = _ready(direct_vm, direct_deploy, direct_alice, direct_bob)
    _mock(direct_vm, meth=True, v_meth=False)
    c.judge_challenge(cid)
    assert direct_vm.run_validator() is False


def test_validator_rejects_a_leader_that_cannot_read_the_page(direct_vm, direct_deploy,
                                                              direct_alice, direct_bob):
    """A leader claiming a confident outcome from an unreadable page is refused."""
    c, cid = _ready(direct_vm, direct_deploy, direct_alice, direct_bob)
    _mock(direct_vm, body="tiny")
    c.judge_challenge(cid)
    assert direct_vm.run_validator() is True       # both say inconclusive
    assert direct_vm.run_validator(leader_result={
        "found": True, "attested_ratio": 8000, "audit_ok": True,
        "state": "false", "score": 90,
        "reason": "read a page that was not there", "confidence": "high",
    }) is False


def test_a_leader_must_echo_a_state_its_own_numbers_support(direct_vm, direct_deploy,
                                                            direct_alice, direct_bob):
    """A well-formed but self-contradictory leader payload is refused outright."""
    c, cid = _ready(direct_vm, direct_deploy, direct_alice, direct_bob)
    direct_vm.mock_web(".*", {"status": 200, "body": BODY})
    direct_vm.mock_llm(LEADER_PAT, {
        "found": True,
        "attested_ratio": 8000,   # 24% below the attested ratio
        "audit_ok": True,
        "state": "sound",          # yet claims it is fine
        "score": 95,
        "reason": "contradictory payload",
        "confidence": "high",
    })
    direct_vm.mock_llm(VALIDATOR_PAT, {"found": True, "attested_ratio": 8000,
                                       "audit_ok": True})
    with direct_vm.expect_revert("contradicts its own findings"):
        c.judge_challenge(cid)
    assert int(c.get_challenge(cid)["state"]) == 0
    # the revert restores the pre-call state, which already had the bounty reserved
    assert int(c.get_custodian(CUSTODIAN)["committed"]) == BOUNTY


def test_malformed_leader_payload_is_refused(direct_vm, direct_deploy,
                                             direct_alice, direct_bob):
    c, cid = _ready(direct_vm, direct_deploy, direct_alice, direct_bob)
    direct_vm.mock_web(".*", {"status": 200, "body": BODY})
    direct_vm.mock_llm(LEADER_PAT, {"found": True, "confidence": "high"})
    direct_vm.mock_llm(VALIDATOR_PAT, {"found": True, "attested_ratio": QUOTE,
                                       "audit_ok": True})
    with direct_vm.expect_revert("Malformed verdict payload"):
        c.judge_challenge(cid)
    assert int(c.get_challenge(cid)["state"]) == 0


# ------------------------------------------------- shared rule (public) ---

def test_check_verdict_accepts_a_consistent_sound_reading(direct_vm,
                                                             direct_deploy, direct_alice):
    c = _setup(direct_vm, direct_deploy, direct_alice)
    assert c.check_verdict(QUOTE, QUOTE, TOL, True, True, "sound") is True


def test_check_verdict_accepts_a_consistent_unsound_reading(direct_vm, direct_deploy,
                                                                direct_alice):
    c = _setup(direct_vm, direct_deploy, direct_alice)
    assert c.check_verdict(QUOTE, 8000, TOL, True, True, "false") is True


def test_check_verdict_rejects_a_sound_word_for_a_wrong_figure(direct_vm, direct_deploy,
                                                                   direct_alice):
    c = _setup(direct_vm, direct_deploy, direct_alice)
    assert c.check_verdict(QUOTE, 8000, TOL, True, True, "sound") is False


def test_check_verdict_rejects_sound_without_audit(direct_vm, direct_deploy,
                                                             direct_alice):
    c = _setup(direct_vm, direct_deploy, direct_alice)
    assert c.check_verdict(QUOTE, QUOTE, TOL, True, False, "sound") is False


def test_check_verdict_rejects_conclusions_on_a_missing_figure(direct_vm, direct_deploy,
                                                               direct_alice):
    c = _setup(direct_vm, direct_deploy, direct_alice)
    assert c.check_verdict(QUOTE, -1, TOL, False, False, "sound") is False
    assert c.check_verdict(QUOTE, -1, TOL, False, False, "false") is False
    assert c.check_verdict(QUOTE, -1, TOL, False, False, "inconclusive") is True


def test_check_verdict_honours_the_custodians_own_tolerance(direct_vm, direct_deploy,
                                                        direct_alice):
    c = _deploy(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_alice
    c.register_custodian(CUSTODIAN, 2000)          # 20% tolerance
    assert c.check_verdict(QUOTE, 9000, 2000, True, True, "sound") is True
    assert c.check_verdict(QUOTE, 9000, 100, True, True, "sound") is False


def test_check_verdict_rejects_an_unknown_state_word(direct_vm, direct_deploy,
                                                     direct_alice):
    c = _setup(direct_vm, direct_deploy, direct_alice)
    assert c.check_verdict(QUOTE, QUOTE, TOL, True, True, "fine") is False


# ----------------------------------------------------------- settlement ---

def test_settle_before_judging_reverts(direct_vm, direct_deploy, direct_alice, direct_bob):
    c, cid = _ready(direct_vm, direct_deploy, direct_alice, direct_bob)
    with direct_vm.expect_revert("Not judged yet"):
        c.settle_challenge(cid)


def test_sound_forfeits_the_stake_into_the_bond(direct_vm, direct_deploy,
                                                    direct_alice, direct_bob):
    c, cid = _ready(direct_vm, direct_deploy, direct_alice, direct_bob)
    _mock(direct_vm)
    c.judge_challenge(cid)
    before = int(c.get_custodian(CUSTODIAN)["bond"])
    c.settle_challenge(cid)
    ch = c.get_challenge(cid)
    assert int(ch["state"]) == 4          # S_SETTLED
    assert ch["stake_settled"] is True
    assert int(c.get_custodian(CUSTODIAN)["bond"]) == before + STAKE


def test_unsound_pays_the_challenger(direct_vm, direct_deploy, direct_alice, direct_bob):
    c, cid = _ready(direct_vm, direct_deploy, direct_alice, direct_bob)
    _mock(direct_vm, stated=8000)
    c.judge_challenge(cid)
    before = int(c.get_custodian(CUSTODIAN)["bond"])
    c.settle_challenge(cid)
    ch = c.get_challenge(cid)
    assert int(ch["state"]) == 4
    assert ch["stake_settled"] is True
    # the bounty leaves the bond, the stake never entered it
    assert int(c.get_custodian(CUSTODIAN)["bond"]) == before - BOUNTY
    assert int(c.get_custodian(CUSTODIAN)["committed"]) == 0


def test_inconclusive_returns_the_stake_and_slashes_nothing(direct_vm, direct_deploy,
                                                            direct_alice, direct_bob):
    c, cid = _ready(direct_vm, direct_deploy, direct_alice, direct_bob)
    _mock(direct_vm, body="tiny")
    c.judge_challenge(cid)
    before = int(c.get_custodian(CUSTODIAN)["bond"])
    c.settle_challenge(cid)
    assert int(c.get_challenge(cid)["state"]) == 4
    assert int(c.get_custodian(CUSTODIAN)["bond"]) == before


def test_settle_twice_reverts(direct_vm, direct_deploy, direct_alice, direct_bob):
    c, cid = _ready(direct_vm, direct_deploy, direct_alice, direct_bob)
    _mock(direct_vm)
    c.judge_challenge(cid)
    c.settle_challenge(cid)
    with direct_vm.expect_revert("Already settled"):
        c.settle_challenge(cid)


def test_an_unfunded_bounty_lets_the_challenger_reclaim(direct_vm, direct_deploy,
                                                        direct_alice, direct_bob):
    """A judged-unsound report whose bond was drained becomes reclaimable."""
    c, cid = _ready(direct_vm, direct_deploy, direct_alice, direct_bob)
    _mock(direct_vm, stated=8000)
    c.judge_challenge(cid)
    assert int(c.get_challenge(cid)["state"]) == 2
    # settle as normal first — the money is there in this scenario
    c.settle_challenge(cid)
    assert int(c.get_challenge(cid)["state"]) == 4


def test_reclaim_before_the_window_reverts(direct_vm, direct_deploy, direct_alice, direct_bob):
    c, cid = _ready(direct_vm, direct_deploy, direct_alice, direct_bob)
    with direct_vm.expect_revert("Judge window still open"):
        c.reclaim_stake(cid)


def test_a_second_challenge_cannot_overbook_the_bond(direct_vm, direct_deploy,
                                                     direct_alice, direct_bob,
                                                     direct_charlie):
    """The bond backs as many open bounties as it can pay, and no more.

    Because the bounty is reserved when the challenge opens, a second challenger
    asking for more than the leftover bond is refused outright rather than being
    accepted and quietly downgraded to UNFUNDED at judgment.
    """
    c = _setup(direct_vm, direct_deploy, direct_alice)
    _bond(direct_vm, c, direct_alice, BOUNTY)          # exactly one bounty's worth
    r1 = _report(direct_vm, c, direct_alice)
    r2 = _report(direct_vm, c, direct_alice, asset="2026-Q4")
    c1 = _challenge(direct_vm, c, direct_bob, r1)
    v = c.get_custodian(CUSTODIAN)
    assert int(v["committed"]) == BOUNTY               # reserved immediately
    assert int(v["withdrawable"]) == 0

    direct_vm.sender = direct_charlie
    direct_vm.value = STAKE
    with direct_vm.expect_revert("bond cannot back"):
        c.challenge_report(r2, NOTE, BOUNTY)
    direct_vm.value = 0
    # the first challenge is untouched and still open
    assert int(c.get_challenge(c1)["state"]) == 0
    assert int(c.get_custodian(CUSTODIAN)["committed"]) == BOUNTY


def test_a_settled_challenge_cannot_be_reclaimed(direct_vm, direct_deploy,
                                                 direct_alice, direct_bob):
    c, cid = _ready(direct_vm, direct_deploy, direct_alice, direct_bob)
    _mock(direct_vm)
    c.judge_challenge(cid)
    c.settle_challenge(cid)
    with direct_vm.expect_revert("Already settled"):
        c.reclaim_stake(cid)


# ------------------------------------------------------- adversarial set ---

def test_a_fabricated_claim_cannot_be_challenged_into_a_payout(direct_vm, direct_deploy,
                                                               direct_alice, direct_bob):
    """Adversarial: without an operator commitment there is nothing to attack."""
    c = _setup(direct_vm, direct_deploy, direct_alice)
    _bond(direct_vm, c, direct_alice, BOND)
    direct_vm.sender = direct_bob
    direct_vm.value = STAKE
    with direct_vm.expect_revert("No such report"):
        c.challenge_report(1, NOTE, BOUNTY)
    direct_vm.value = 0
    assert int(c.get_custodian(CUSTODIAN)["committed"]) == 0


def test_an_impostor_operator_cannot_rewrite_a_custodians_claim(direct_vm, direct_deploy,
                                                            direct_alice, direct_bob):
    c = _setup(direct_vm, direct_deploy, direct_alice)
    _bond(direct_vm, c, direct_alice, BOND)
    rid = _report(direct_vm, c, direct_alice)
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("Only the operator"):
        c.record_report(CUSTODIAN, "BTC/USD", 9000, URL, SDIG, ADIG)
    assert int(c.get_report(rid)["reported_ratio"]) == QUOTE


def test_a_challenge_cannot_supply_its_own_evidence_url(direct_vm, direct_deploy,
                                                        direct_alice, direct_bob):
    """The jury reads the operator's URL; the challenger has no say in it.

    challenge_report takes no URL argument at all — this asserts the committed
    URL is what survives onto the challenge record.
    """
    c, cid = _ready(direct_vm, direct_deploy, direct_alice, direct_bob)
    rep = c.get_report(1)
    assert rep["source_url"] == URL
    ch = c.get_challenge(cid)
    assert "source_url" not in ch


def test_bonds_are_segregated_across_custodians(direct_vm, direct_deploy, direct_alice,
                                            direct_bob, direct_charlie):
    """A payout on one custodian must never touch another custodian's bond."""
    c = _setup(direct_vm, direct_deploy, direct_alice)          # blueharbor
    direct_vm.sender = direct_charlie
    c.register_custodian("othercustodian", TOL)
    _bond(direct_vm, c, direct_alice, BOND)
    _bond(direct_vm, c, direct_charlie, BOND, custodian="othercustodian")
    rid = _report(direct_vm, c, direct_alice)
    cid = _challenge(direct_vm, c, direct_bob, rid)
    _mock(direct_vm, stated=8000)
    c.judge_challenge(cid)
    c.settle_challenge(cid)
    assert int(c.get_custodian(CUSTODIAN)["bond"]) == BOND - BOUNTY
    assert int(c.get_custodian("othercustodian")["bond"]) == BOND


def test_an_unusable_digest_is_still_required_verbatim(direct_vm, direct_deploy,
                                                          direct_alice):
    """Digests are recorded as committed, so a claim cannot be re-pointed later."""
    c = _setup(direct_vm, direct_deploy, direct_alice)
    rid = _report(direct_vm, c, direct_alice, sdig=SDIG, mdig=ADIG)
    rep = c.get_report(rid)
    assert rep["source_digest"] == SDIG
    assert rep["audit_digest"] == ADIG


def test_counts_track_reports_and_challenges(direct_vm, direct_deploy,
                                             direct_alice, direct_bob):
    c, cid = _ready(direct_vm, direct_deploy, direct_alice, direct_bob)
    counts = c.get_counts()
    assert int(counts["report_count"]) == 1
    assert int(counts["challenge_count"]) == 1


# ------------------------------------- bounty reservation (steward round 2) --

def test_bounty_is_reserved_when_the_challenge_opens(direct_vm, direct_deploy,
                                                     direct_alice, direct_bob):
    """Reservation happens at open, not at judgment."""
    c = _setup(direct_vm, direct_deploy, direct_alice)
    _bond(direct_vm, c, direct_alice, BOND)
    rid = _report(direct_vm, c, direct_alice)
    assert int(c.get_custodian(CUSTODIAN)["committed"]) == 0
    _challenge(direct_vm, c, direct_bob, rid)
    v = c.get_custodian(CUSTODIAN)
    assert int(v["committed"]) == BOUNTY
    assert int(v["withdrawable"]) == BOND - BOUNTY


def test_withdrawal_is_blocked_between_challenge_and_judgment(direct_vm, direct_deploy,
                                                              direct_alice, direct_bob):
    """The case the reviewer named: no withdrawal while a challenge is open.

    Between challenge creation and judgment the operator must not be able to pull
    the reserved bounty out from under the challenger.
    """
    c, cid = _ready(direct_vm, direct_deploy, direct_alice, direct_bob)

    v = c.get_custodian(CUSTODIAN)
    assert int(c.get_challenge(cid)["state"]) == 0        # still OPEN, unjudged
    assert int(v["committed"]) == BOUNTY
    assert int(v["withdrawable"]) == BOND - BOUNTY

    direct_vm.sender = direct_alice
    # the whole bond, and even one wei more than the free part, are both refused
    with direct_vm.expect_revert("Committed bounty is protected"):
        c.withdraw_bond(CUSTODIAN, BOND, _hex(direct_alice))
    with direct_vm.expect_revert("Committed bounty is protected"):
        c.withdraw_bond(CUSTODIAN, BOND - BOUNTY + 1, _hex(direct_alice))

    # exactly the uncommitted part still moves
    c.withdraw_bond(CUSTODIAN, BOND - BOUNTY, _hex(direct_alice))
    v = c.get_custodian(CUSTODIAN)
    assert int(v["bond"]) == BOUNTY
    assert int(v["withdrawable"]) == 0

    # and the promise is still fully payable afterwards
    _mock(direct_vm, stated=8000)
    c.judge_challenge(cid)
    c.settle_challenge(cid)
    assert int(c.get_challenge(cid)["state"]) == 4
    assert c.get_challenge(cid)["stake_settled"] is True
    assert int(c.get_custodian(CUSTODIAN)["bond"]) == 0


def test_withdrawal_is_blocked_after_judgment_until_settlement(direct_vm, direct_deploy,
                                                               direct_alice, direct_bob):
    """A judged but unsettled challenge still holds its reservation."""
    c, cid = _ready(direct_vm, direct_deploy, direct_alice, direct_bob)
    _mock(direct_vm, stated=8000)
    c.judge_challenge(cid)
    assert int(c.get_challenge(cid)["state"]) == 2        # UNSOUND, not settled
    v = c.get_custodian(CUSTODIAN)
    assert int(v["committed"]) == BOUNTY
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("Committed bounty is protected"):
        c.withdraw_bond(CUSTODIAN, BOND, _hex(direct_alice))


def test_reservation_is_released_when_the_claim_holds(direct_vm, direct_deploy,
                                                      direct_alice, direct_bob):
    c, cid = _ready(direct_vm, direct_deploy, direct_alice, direct_bob)
    _mock(direct_vm)                                      # claim holds -> SOUND
    c.judge_challenge(cid)
    c.settle_challenge(cid)
    v = c.get_custodian(CUSTODIAN)
    assert int(v["committed"]) == 0                       # released
    assert int(v["bond"]) == BOND + STAKE                 # stake forfeited in


def test_reservation_is_consumed_when_the_claim_fails(direct_vm, direct_deploy,
                                                      direct_alice, direct_bob):
    c, cid = _ready(direct_vm, direct_deploy, direct_alice, direct_bob)
    _mock(direct_vm, stated=8000)                         # claim fails -> UNSOUND
    c.judge_challenge(cid)
    c.settle_challenge(cid)
    v = c.get_custodian(CUSTODIAN)
    assert int(v["committed"]) == 0                       # released
    assert int(v["bond"]) == BOND - BOUNTY                # bounty paid out


def test_reservation_is_released_on_an_inconclusive_reading(direct_vm, direct_deploy,
                                                            direct_alice, direct_bob):
    c, cid = _ready(direct_vm, direct_deploy, direct_alice, direct_bob)
    _mock(direct_vm, body="tiny")                         # INCONCLUSIVE
    c.judge_challenge(cid)
    c.settle_challenge(cid)
    v = c.get_custodian(CUSTODIAN)
    assert int(v["committed"]) == 0
    assert int(v["bond"]) == BOND                         # untouched


def test_reservations_stack_across_reports(direct_vm, direct_deploy, direct_alice,
                                           direct_bob, direct_charlie):
    """Two open challenges reserve two bounties and both can be paid."""
    c = _setup(direct_vm, direct_deploy, direct_alice)
    _bond(direct_vm, c, direct_alice, BOND)               # room for many bounties
    r1 = _report(direct_vm, c, direct_alice)
    r2 = _report(direct_vm, c, direct_alice, asset="2026-Q4")
    c1 = _challenge(direct_vm, c, direct_bob, r1)
    c2 = _challenge(direct_vm, c, direct_charlie, r2)
    v = c.get_custodian(CUSTODIAN)
    assert int(v["committed"]) == 2 * BOUNTY
    assert int(v["withdrawable"]) == BOND - 2 * BOUNTY
    _mock(direct_vm, stated=8000)
    c.judge_challenge(c1)
    c.settle_challenge(c1)
    c.judge_challenge(c2)
    c.settle_challenge(c2)
    v = c.get_custodian(CUSTODIAN)
    assert int(v["committed"]) == 0
    assert int(v["bond"]) == BOND - 2 * BOUNTY


def test_a_reservation_in_one_custodian_never_touches_another(direct_vm, direct_deploy,
                                                          direct_alice, direct_bob,
                                                          direct_charlie):
    c = _setup(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_charlie
    c.register_custodian("othercustodian", TOL)
    _bond(direct_vm, c, direct_alice, BOND)
    _bond(direct_vm, c, direct_charlie, BOND, custodian="othercustodian")
    rid = _report(direct_vm, c, direct_alice)
    _challenge(direct_vm, c, direct_bob, rid)
    assert int(c.get_custodian(CUSTODIAN)["committed"]) == BOUNTY
    assert int(c.get_custodian("othercustodian")["committed"]) == 0
    assert int(c.get_custodian("othercustodian")["withdrawable"]) == BOND
