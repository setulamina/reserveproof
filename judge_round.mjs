// Definitive live round for ReserveProof.
//
// The evidence URL is derived from the local fixture, so this script works whether or
// not the repository has been published: reserve_fixture.md is base64-encoded and served
// back verbatim by httpbin.
//
//   GL_PK=0x<custodian operator key>  GL_PK_CH=0x<challenger key>  node judge_round.mjs
//
// Round A claims 105.00% — the fixture states 105.00% with an identified audit, so it
// derives `sound` and the challenger's stake is forfeited.
// Round B claims 80.00% against the same fixture, so it derives `false` and the
// challenger is paid the stake plus the bounty out of the bond.
//
// Keys are read from the environment on purpose — never commit them.

import { createClient, createAccount } from "genlayer-js";
import { studionet } from "genlayer-js/chains";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ADDR = "0xc244838A87a03DAcEA765B0dCFd093ae7B422347";
const SDIG = "sha256:page-2f9a1c7b77c1";
const ADIG = "sha256:audit-77be0d31";
const O = "anchorcustody";
const ASSET_NAME = "USDC";
const STAKE = 100000000000000000n;   // 0.1 GEN
const BOUNTY = 20000000000000000n;   // 0.02 GEN

const here = path.dirname(fileURLToPath(import.meta.url));
const fixture = fs.readFileSync(path.join(here, "reserve_fixture.md"), "utf-8");
const SRC = "https://httpbin.org/base64/" + Buffer.from(fixture, "utf-8").toString("base64");

const PK = process.env.GL_PK, PK_CH = process.env.GL_PK_CH;
if (!PK || !PK.startsWith("0x") || !PK_CH || !PK_CH.startsWith("0x")) {
  console.error("Set GL_PK (custodian operator) and GL_PK_CH (challenger) to 0x-prefixed private keys.");
  process.exit(1);
}

const OP = createAccount(PK), CH = createAccount(PK_CH);
const reader = createClient({ chain: studionet });
const mk = (a) => {
  const c = createClient({ chain: studionet, account: a });
  c.estimateTransactionGas = async () => 3_000_000n;
  return c;
};
async function wait(hash) {
  for (let i = 0; i < 220; i++) {
    await new Promise((r) => setTimeout(r, 5000));
    let tx = null;
    try { tx = await reader.getTransaction({ hash }); } catch { continue; }
    if (!tx) continue;
    const st = String(tx.statusName || tx.status || "");
    if (st === "FINALIZED" || st === "ACCEPTED") {
      return { status: st, exec: String(tx.consensus_data?.leader_receipt?.[0]?.execution_result || "") };
    }
    if (st === "UNDETERMINED" || st === "LEADER_TIMEOUT") return { status: st, exec: "CONSENSUS_FAIL" };
  }
  return { status: "TIMEOUT", exec: "" };
}
async function send(label, acct, fn, args, value = 0n) {
  try {
    const hash = await mk(acct).writeContract({ address: ADDR, functionName: fn, args, value });
    const r = await wait(hash);
    console.log(`[${label}] ${fn} -> ${r.exec === "SUCCESS" ? "return" : r.exec || "?"} ${hash}`);
    return r;
  } catch (e) {
    console.log(`[${label}] ${fn} -> THREW ${String(e?.details || e?.message || e).split("\n")[0].slice(0, 140)}`);
    return { exec: "THREW" };
  }
}
const view = async (fn, args) => {
  try { return await reader.readContract({ address: ADDR, functionName: fn, args }); }
  catch (e) { return { error: String(e?.message || e).slice(0, 120) }; }
};
const STATE = ["OPEN", "SOUND", "FALSE", "INCONCLUSIVE", "SETTLED", "RELEASED"];

console.log("evidence URL:", SRC.length, "chars");
console.log("operator:", OP.address, "| challenger:", CH.address);
console.log("custodian before:", JSON.stringify(await view("get_custodian", [O])));
console.log("");

async function round(label, ratio, note) {
  await send(label, OP, "record_report", [O, ASSET_NAME, ratio, SRC, SDIG, ADIG]);
  const rid = BigInt((await view("get_counts", [])).report_count);
  await send(label, CH, "challenge_report", [rid, note, BOUNTY], STAKE);
  const cid = BigInt((await view("get_counts", [])).challenge_count);
  await send(label, CH, "judge_challenge", [cid]);
  const j = await view("get_challenge", [cid]);
  console.log(`${label} verdict: ${STATE[Number(j.state)]} | attested ${j.attested_ratio} bps | score ${j.score}`);
  await send(label, CH, "settle_challenge", [cid]);
  console.log(`${label} settled:`, (await view("get_challenge", [cid])).stake_settled);
  console.log("");
}

// A: honest quote -> sound, stake forfeited into the bond
await round("A", 10500n,
  "checking whether the audit really states this reserve ratio for the asset");

// B: inflated quote -> false, challenger paid from the bond
await round("B", 8000n,
  "the attestation states 105.00 percent coverage, far above the claimed 80.00 percent");

console.log("FINAL custodian:", JSON.stringify(await view("get_custodian", [O])));
console.log("FINAL counts:", JSON.stringify(await view("get_counts", [])));
