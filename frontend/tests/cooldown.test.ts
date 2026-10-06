// A halted lesson's cooldown on the course map (src/lib/cooldown.ts, SPEC §6.3).
import assert from "node:assert/strict";
import { test } from "node:test";
import { cooldownEnd, isCooldownMessage, returnTimeFromMessage, returnTimePhrase } from "../src/lib/cooldown.ts";

const at = (h: number, m: number, dayOffset = 0): number => new Date(2026, 9, 6 + dayOffset, h, m).getTime();

test("cooldownEnd: only a cooldown still ahead counts", () => {
  const now = at(12, 44);
  const until = new Date(at(14, 43)).toISOString();
  assert.equal(cooldownEnd(until, now), at(14, 43));
  assert.equal(cooldownEnd(until, at(14, 43)), null); // over at the minute it ends
  assert.equal(cooldownEnd(until, at(15, 0)), null);
  assert.equal(cooldownEnd(null, now), null);
  assert.equal(cooldownEnd(undefined, now), null);
  assert.equal(cooldownEnd("not a date", now), null);
});

test("returnTimePhrase: the local return time, with the day when it isn't today", () => {
  assert.equal(returnTimePhrase(at(14, 43), at(12, 44), "en-GB"), "at 14:43");
  assert.equal(returnTimePhrase(at(9, 5), at(12, 44), "en-US"), "at 09:05");
  assert.equal(returnTimePhrase(at(0, 15, 1), at(23, 10), "en-GB"), "tomorrow at 00:15");
  assert.match(returnTimePhrase(at(9, 0, 3), at(12, 0), "en-GB"), /^on \w+ at 09:00$/);
});

test("the server's 409: recognised, and its time read back", () => {
  const message = "This lesson comes back at 14:43 (local time) with fresh questions.";
  assert.equal(isCooldownMessage(message), true);
  assert.equal(returnTimeFromMessage(message), "at 14:43");
  assert.equal(returnTimeFromMessage("This lesson comes back at 2:05 PM (local time) with fresh questions."), "at 2:05 PM");
  assert.equal(returnTimeFromMessage("This lesson comes back later."), null);
  // Other 409s are not cooldowns.
  assert.equal(isCooldownMessage("S3 is locked — learn S1 (Complex numbers) first."), false);
  assert.equal(isCooldownMessage("Finish or leave 'Complex numbers' first."), false);
});
