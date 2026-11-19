# ReserveProof

**A challenge market over proof-of-reserve attestations, judged on-chain.**

A custodian says "our reserves cover 105% of liabilities". Nobody can check that, and
nothing follows when the auditor's page says something smaller. ReserveProof makes each
claim checkable, and puts money behind being wrong.

- **Primary tag:** DeFi
- **Sub-tags:** Yield Optimization · Treasury Rebalancing
- **Network:** GenLayer Studionet (chain 61999)
- **Contract:** [`0xc244838A87a03DAcEA765B0dCFd093ae7B422347`](https://explorer-studio.genlayer.com/address/0xc244838A87a03DAcEA765B0dCFd093ae7B422347)
- **Deploy tx:** `0xb38d3f87c21d102b54f705c7f006899db1843b0f0cac9f5524a8c814029602d5` — ACCEPTED, MAJORITY_AGREE, `execution_result` SUCCESS
- **Size:** 27,582 bytes · **14 public methods** · **73 direct-mode tests passing**
- **Admin:** the deploying account (the custodian of the first bond, not a privileged role)

---

## The problem this addresses

Proof of reserves is published as a PDF and consumed as a rumour. A depositor has no way
to test the number, no way to know whether the page still says what it said last quarter,
and no consequence attaches when the claim quietly becomes false.

ReserveProof turns the attestation into a bonded claim with a challenge market attached.

## Who commits what

**The party with authority commits the evidence; the party with a grievance can only
reference it.**

| Party | Can do | Cannot do |
|---|---|---|
| Custodian | `register_custodian`, `record_report`, `withdraw_bond` | challenge their own claim, withdraw a reserved bounty |
| Anyone | `fund_bond`, `challenge_report`, `settle_challenge`, `reclaim_stake` | record a claim on someone else's custodian |

`record_report` binds, in one commitment: the custodian id, the asset, the claimed reserve
ratio **in basis points**, the auditor's page URL, a digest of that page and a digest of
the audit engagement. Only the custodian may record, so a challenger cannot invent a
number to attack.

`challenge_report` takes **no URL argument**. A challenger stakes GEN and gives a reason;
the jury reads the URL the custodian committed. A challenger therefore cannot point the
jury at a page of their own choosing.

## How the verdict is derived

```
derive(reported, attested, tolerance, found, audit_ok):
    page unreadable, or no usable ratio ...... inconclusive
    |attested - reported| * 10000 > tol * attested ... false
    within tolerance, audit_ok false ......... false
    within tolerance, audit_ok true .......... sound
```

**Deviation is measured against the ATTESTED ratio, not the claim.** A custodian reporting
80% coverage when the auditor states 105% is 24% low, and the check must fail by that
magnitude regardless of which side you divide by. An inflated (or deflated) claim can never
be "close enough to itself".

`tolerance_bps` is chosen by the custodian at registration (basis points, capped at 2000),
so a stablecoin custodian and a long-tail asset custodian can use different bands without
changing the rule.

1. **An unreadable page slashes nothing.** Fetch failure, no usable figure, or an
   unparseable payload all resolve to `inconclusive` and the challenger gets the stake
   back. Ambiguity never costs anyone money.
2. **A ratio outside tolerance is false regardless of audit quality.** A well-documented
   engagement cannot rescue a number the auditor did not state.
3. **The rule is public.** `check_verdict(reported, attested, tolerance, found, audit_ok,
   leader_state)` returns the same boolean consensus stops on — seven live calls below.

### Independent validator evaluation

Every validator re-fetches the committed URL, re-extracts the ratio and the engagement
judgement with its own prompt, and re-derives with the same `_derive`. It reports
disagreement when:

- its own derived outcome differs from the leader's echoed `state`, **or**
- its own extracted ratio differs from the leader's beyond the custodian's tolerance, **or**
- the two disagree on `audit_ok`.

After consensus, `judge_challenge` also refuses deterministically:

- a payload missing any of the seven required keys (`Malformed verdict payload`), and
- a payload whose echoed `state` is not what `_derive` produces from **its own numbers**
  (`Verdict contradicts its own findings`).

## Money

- **Per-custodian bonds.** A payout on `anchorcustody` can never touch another custodian's
  bond; there is a test for it.
- **The bounty is reserved when the challenge opens.** `challenge_report` calls `_reserve`,
  which requires `bounty ≤ bond − committed` and adds it to `committed` in the same call.
  Reservation is atomic with the challenge being created.
- **This is what makes concurrency safe.** A second challenger asking for money the bond
  cannot spare is **refused at open**, not accepted and quietly downgraded later.
  `committed ≤ bond` holds as an invariant.
- **The reserved amount is unavailable to the custodian for the whole life of the
  challenge** — including after judgment and before settlement.
- **Judgment moves no money.** The verdict only decides which way the reservation goes.
- **Settlement pays or releases it, exactly once.** `false` pays `stake + bounty` and draws
  the bounty out of the bond; `sound` releases the reservation and forfeits the stake into
  the bond; `inconclusive` releases it and returns the stake.
- **`reclaim_stake` releases the reservation too**, so an unjudged challenge that times out
  cannot lock the bond forever.

Transfers use `emit_transfer(..., on='finalized')`.

## Test coverage — 73 tests, all passing

```
tests/direct/test_reserve_proof.py ....................... 73 passed
```

| Area | Tests | Notable adversarial cases |
|---|---|---|
| Custodian registry | 6 | re-registration, tolerance over the 2000 cap, id normalisation |
| Bond pool | 7 | non-custodian withdraw, withdrawing a reserved bounty |
| Claim commitment | 8 | **third party recording on someone else's custodian**, missing asset, non-http URL, missing digests, implausible ratio |
| Challenge | 9 | **custodian challenging their own claim**, stake/bounty bounds, 100x cap, **bounty the bond cannot back**, one challenge per report |
| Judging | 10 | sound, inside tolerance, outside tolerance, engagement not identified, unreadable page, no ratio stated, re-judging, **validator disagreement on the ratio**, **disagreement on the engagement**, **self-contradictory leader**, malformed payload |
| Shared rule (`check_verdict`) | 7 | wrong word for a wrong ratio, sound without an engagement, conclusions on a missing ratio, custodian-specific tolerance, unknown state word |
| Settlement | 8 | settle before judging, sound forfeits stake, false pays, inconclusive slashes nothing, double settle, reclaim before window |
| **Bounty reservation** | **8** | **withdrawal attempted between challenge creation and judgment**, **withdrawal blocked after judgment until settlement**, second challenge **cannot overbook** the bond, released/consumed/released per outcome, reservations stack, one custodian's reservation never touches another |
| Adversarial set | 5 | **fabricated claim (no report)**, **impostor custodian**, **challenger cannot supply a URL**, **bond segregation**, digests recorded verbatim |

Validator-disagreement tests use the harness's `run_validator` replay — the real captured
validator function, not a re-implementation.

## Verified live on Studionet

Two funded accounts: the custodian and an unrelated challenger. Every
`bond / committed / withdrawable` figure was read back with `get_custodian` after the fact.

`execution_result` is the leader receipt's status — that is the field that distinguishes a
return from a rollback. `result_name` is the consensus outcome and is identical either way.

| # | Call | Result | bond / committed / withdrawable | Tx |
|---|---|---|---|---|
| 1 | `gen_getContractSchema` | **14 methods** | — | — |
| 2 | `register_custodian("anchorcustody", 100)` | **return** | — | `0x2efabf65246b12fcbcf058117a13ff77caab91025e8ab7026e18a03f43dca684` |
| 3 | `fund_bond` 1.0 GEN | **return** | 1.00 / 0.00 / 1.00 | `0xadffa40630c7f8f3e73cb91f8c52ca759a0d5dafab106678755b3ad4b99c8ecf` |
| 4 | **`record_report` by the non-custodian** | **ERROR** ✅ | 1.00 / 0.00 / 1.00 | `0x5c034f2a4318d8fd1373d174b01e939458913fd938906f3e594bbb2e159efe83` |
| 5 | `record_report("USDC", 10500)` | **return** | 1.00 / 0.00 / 1.00 | `0xd236f51408bb0f38c9fd60d16759f8fbc4e11e0942a9299a75be69ab85ed01ee` |
| 6 | `challenge_report` bounty 0.02 | **return** — reserved **on open** | 1.00 / **0.02** / **0.98** | `0xde7d8ce015e352375e67cdc48909628b8520bf3587814455e6a1963a6988fcb1` |
| 7 | **`withdraw_bond` 1.00** (challenge OPEN) | **ERROR** ✅ | 1.00 / 0.02 / 0.98 | `0x3c6c2ba59361ee43ceb352556e09437c8b1e5e2e10a6bc7febfbc4218aafedf4` |
| 8 | **`withdraw_bond` 0.99** (1 wei over free) | **ERROR** ✅ | 1.00 / 0.02 / 0.98 | `0xccffc0a345d27976605cac9c847c98de3fbc147391529eb1cfd5ed12f9851f60` |
| 9 | `withdraw_bond` 0.98 (exactly free) | **return** | 0.02 / 0.02 / 0.00 | `0xd56fc63a047c3a40c9cb2c9dbd2c09d89abea32fd483da0d03b0d6c198a2310a` |
| 10 | `judge_challenge` | **return** — verdict **SOUND**, attested 10500 bps, score 92 | 0.02 / 0.02 / 0.00 | `0x59e8103c8cb46c6a148d72d6d4c0bd699640c3b2b985a2f66005c32707ec6669` |
| 11 | **`withdraw_bond` 0.01** (judged, unsettled) | **ERROR** ✅ | 0.02 / 0.02 / 0.00 | `0xb5b5b7cd3de74c67af5fb42661f8909427f438d1495f1e86097dab51347a6711` |
| 12 | `settle_challenge` | **return** — released, stake forfeited in | **0.12** / **0.00** / 0.12 | `0x38fee9d1005f8d2f7e7409f0f429a2e53b021b720f2c3003da8293d346586b40` |
| 13 | `withdraw_bond` 0.12 (all free) | **return** | 0.00 / 0.00 / 0.00 | `0xa7197240354f4d649db664e9ca8f4fe8cb970d1963fddf8187acaf3a63c59d13` |

Rows 7, 8 and 11 are the reservation guarantee demonstrated on chain: while a challenge is
open — and again after it is judged but before it settles — the custodian can reach **none**
of the reserved amount.

### A false claim pays the challenger

| Call | Result | Tx |
|---|---|---|
| `fund_bond` 0.2 GEN | **return** | `0x4bec64a701b95e47a22ba3e63084c81744bf4ca9b0a6eabc6e83cdfd6951639e` |
| `record_report("USDC", 8000)` — 24% below the attestation | **return** | `0x71ffd52c764971c2faff88b89ce4e6623db5417c866d857dbbbb10d165508653` |
| `challenge_report` bounty 0.02, reserved (0.18 free) | **return** | `0xea2d3f073076756a8521687e6351aa84b7a1a09008ec5c388c93fa1850fa86b3` |
| `judge_challenge` | **return** — verdict **FALSE** | `0x2fd2c9b0bf75e06952e6e383a876494c2d10bf6b3cc945e95bfc14a846bc77d8` |
| `settle_challenge` | **return** — challenger paid stake + 0.02 GEN, bond 0.20 → **0.18** | `0x14c5075d6f9e85dd89644fd68d8bd98994bc4cbe5b62bb31e8eeb4f6ade04a98` |

Final `get_counts`: 2 reports, 2 challenges. Evidence page: the base64 fixture at a
467-char URL, read successfully in both rounds.

### `check_verdict` live results — 7/7 as intended

| reported | attested | tol | found | audit_ok | word | result |
|---|---|---|---|---|---|---|
| 10500 | 10500 | 100 | true | true | sound | **true** |
| 10500 | 8000 | 100 | true | true | false | **true** |
| 10500 | 8000 | 100 | true | true | sound | **false** |
| 10500 | 10500 | 100 | true | false | sound | **false** |
| 10500 | -1 | 100 | false | false | inconclusive | **true** |
| 10500 | 10550 | 100 | true | true | sound | **true** |
| 10500 | 10500 | 100 | true | true | fine | **false** |

Row six is the tolerance band working: an attested ratio 0.5% off the claim is inside a 1%
band and the claim holds.

## Known platform behaviour

A `gl.vm.UserError` revert on a **payable** call in this environment does not return the
attached value to the sender — the GEN stays in the contract, and no function can move it
afterwards. The frontend therefore re-reads the report and the custodian before submitting
`challenge_report` and refuses to send value when the challenge would be rejected anyway:
already challenged, caller is the custodian, stake or bounty out of bounds, or **bounty
larger than the custodian's currently uncommitted bond**.

## Repository layout

```
contracts/reserve_proof.py            the contract (27,582 bytes, 14 public methods)
tests/direct/test_reserve_proof.py    73 tests, incl. reservation + adversarial suites
index.html                            the dApp (wallet-pinned genlayer-js client)
reserve_fixture.md                    sample attestation page stating 105.00%
judge_round.mjs                       replays the verdict rounds against the fixture
genlayer.config.yaml                  network config
ruff.toml                             lint config
```

## Running it

```bash
python -m pytest tests/direct/ -v        # 73 tests
```

Deploy (Studionet, chain 61999):

```js
import { createClient, createAccount } from "genlayer-js";
import { studionet } from "genlayer-js/chains";

const client = createClient({ chain: studionet, account: createAccount(PRIVATE_KEY) });
client.estimateTransactionGas = async () => 3_000_000n;   // see note below
const hash = await client.deployContract({ code, args: [] });
```

The contract address of a Studio deploy is `tx.recipient` on the deploy transaction.

## Two implementation notes worth keeping

**Override `estimateTransactionGas`.** When a call is *expected* to revert, gas estimation
throws and the SDK falls back to `200_000n`, which starves legitimate calls too. Pinning it
to 3M is what makes a verification script that deliberately includes refusals run at all.

**Address arguments are typed `str` and parsed inside the contract.** `withdraw_bond` takes
`to: str`, not `to: Address`. GenVM rejects `Address`-typed public parameters on chain even
though the direct VM accepts them.

## What this contract deliberately does not do

- It does not audit the auditor. It checks that the claim matches the committed page.
- It does not authenticate the custodian against a real-world legal entity. It authenticates
  that **this** custodian committed **this** claim.
- It does not let the challenger supply evidence. Their only inputs are a stake and a reason.
