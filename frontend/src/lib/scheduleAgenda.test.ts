import { describe, expect, it } from "vitest";

import { agendaRows, type DayAgenda } from "@/lib/scheduleAgenda";

const day = (fields: Partial<DayAgenda> = {}): DayAgenda => ({
  day: "2026-11-11",
  slots: [],
  twists: [],
  wake: null,
  sleep: null,
  bath: null,
  trip: null,
  ...fields,
});

describe("agendaRows", () => {
  it("sorts the day's arrangements by their start and keeps the clock as written", () => {
    const rows = agendaRows(
      day({
        wake: "07:30",
        slots: [
          { start: "19:00", end: "21:00", what: "晚上练习", where: "事务所", kind: "lesson", with: [] },
          { start: "09:00", end: "12:00", what: "上课", where: "学校", kind: "school", with: [] },
        ],
      }),
    );

    expect(rows.map((row) => [row.time, row.what, row.where])).toEqual([
      ["07:30", "起床", undefined],
      ["09:00–12:00", "上课", "学校"],
      ["19:00–21:00", "晚上练习", "事务所"],
    ]);
  });

  it("puts a bath and a sleep past midnight after the evening", () => {
    const rows = agendaRows(
      day({
        slots: [{ start: "20:00", end: "22:00", what: "练习", where: "事务所", kind: "lesson", with: [] }],
        bath: { at: "00:10", minutes: 30 },
        sleep: "00:40",
      }),
    );

    expect(rows.map((row) => row.key)).toEqual(["slot:0", "bath", "sleep"]);
  });

  it("has no rows without a schedule or for a day with nothing written", () => {
    expect(agendaRows(null)).toEqual([]);
    expect(agendaRows(day())).toEqual([]);
  });
});
