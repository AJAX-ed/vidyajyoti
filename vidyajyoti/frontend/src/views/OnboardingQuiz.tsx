import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { toast } from "sonner";
import {
  BookOpen, Sunrise, Moon, Sun, Utensils, Brain, CalendarDays, ChevronLeft, ChevronRight,
  School, Coffee, Bus, Dumbbell, Shower, Sparkles,
} from "lucide-react";

/* ================= Types ================= */
export type SlotType = "study" | "meal" | "school" | "routine" | "break" | "sleep" | "travel";
export interface Slot {
  start: number; // minutes since 00:00
  end: number;
  type: SlotType;
  label: string;
}

const BACKEND_URL = import.meta.env.VITE_BACKEND_URL || "http://localhost:8000";

/* ================= Time helpers ================= */
function t2m(timeString: string): number {
  const [h, m] = timeString.split(":").map(Number);
  return h * 60 + m;
}
function m2t(minutes: number): string {
  const mm = ((minutes % 1440) + 1440) % 1440;
  const h = Math.floor(mm / 60);
  const m = mm % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

/* ================= Rule-based day plan generator ================= */
interface Answers {
  educationType: "School" | "Home School" | "Coaching";
  coachingSubtype: "Dummy" | "Residential";
  grade: string;
  exams: string[];
  morningTasks: { label: string; minutes: number }[];
  wakeTime: string;
  sleepTime: string;
  leaveHome: string;   // when school/coaching starts (after commute)
  backHome: string;    // when back home
  commuteMinutes: number;
  coachingStart: string;
  coachingEnd: string;
  breakfast: string;
  lunch: string;
  dinner: string;
  mealsPerDay: number;
  peakProductivity: "Morning" | "Afternoon" | "Evening" | "Night";
  studySessionMinutes: number;
  breakMinutes: number;
}

/* Peak-productivity windows (minutes since midnight) */
const PEAK_WINDOWS: Record<Answers["peakProductivity"], [number, number]> = {
  Morning: [5 * 60, 12 * 60],
  Afternoon: [12 * 60, 17 * 60],
  Evening: [17 * 60, 22 * 60],
  Night: [20 * 60, 23 * 60 + 59],
};

function overlapsPeak(s: number, e: number, peak: [number, number]): boolean {
  return s < peak[1] && e > peak[0];
}

function fillStudySessions(
  slots: Slot[], start: number, end: number,
  sessionMin: number, breakMin: number, counter: { n: number },
  peak: [number, number]
) {
  // Sanitize inputs so the loop can never stall or produce 0-hour plans
  const sess = Math.min(Math.max(sessionMin || 50, 15), 180);
  const brk = Math.min(Math.max(breakMin || 10, 5), 30);
  const gap = end - start;
  if (gap < 20) {
    if (gap >= 5) slots.push({ start, end, type: "break", label: "Short break / free time" });
    return;
  }
  let cur = start;
  let guard = 0; // hard safety cap — impossible to hang
  while (end - cur >= sess && guard++ < 100) {
    counter.n += 1;
    const isPeak = overlapsPeak(cur, cur + sess, peak);
    slots.push({
      start: cur, end: cur + sess, type: "study",
      label: `Study session ${counter.n}${isPeak ? " ⚡ peak focus" : ""}`,
    });
    cur += sess;
    // add a break only if a full session still fits after it
    if (end - cur >= brk + sess) {
      slots.push({ start: cur, end: cur + brk, type: "break", label: "Break" });
      cur += brk;
    } else {
      break;
    }
  }
  const leftover = end - cur;
  if (leftover >= 5) slots.push({ start: cur, end, type: "break", label: "Free time" });
}

function generateDayPlan(a: Answers): Slot[] {
  const rawWake = t2m(a.wakeTime);
  // Wake/sleep sanity: sleep must be later the SAME day; if the user picked an
  // earlier time (e.g. wake 06:00, sleep 22:30 stored as 05:00), roll it over.
  let sleepRaw = t2m(a.sleepTime);
  if (sleepRaw <= rawWake) sleepRaw += 1440; // next-day early morning
  const DAY_CAP = 1439;
  // If the rolled-over sleep exceeds midnight, shift BOTH wake and sleep back
  // by the overflow so the plan fits inside a single 00:00–23:59 canvas while
  // preserving its exact shape (relative durations).
  const shift = Math.max(0, sleepRaw - DAY_CAP);
  const wake = rawWake - shift;
  const sleep = sleepRaw - shift;

  const fixed: Slot[] = [];

  // Morning routine starting at wake time — capped so it NEVER swallows
  // school/commute/study time (this was the "breakfast after school" bug).
  let cursor = wake;
  for (const task of a.morningTasks) {
    const dur = Math.max(Number(task.minutes) || 10, 5);
    const rStart = cursor;
    const rEnd = Math.min(cursor + dur, sleep);
    if (rEnd - rStart >= 5) fixed.push({ start: rStart, end: rEnd, type: "routine", label: task.label });
    cursor = rEnd;
    if (cursor >= sleep) break;
  }

  // Meals — anchored inside the waking day, clamped between wake and sleep.
  // If a meal collides with the routine chain, push it right after the routine.
  // Breakfast additionally never lands after school/coaching starts.
  const relTime = (ts: string) => { let m = t2m(ts); if (m < wake) m += 1440; return m - shift; };
  const schoolStart =
    a.educationType === "School" || (a.educationType === "Coaching" && a.coachingSubtype === "Dummy")
      ? relTime(a.leaveHome)
      : a.educationType === "Coaching" && a.coachingSubtype === "Residential"
        ? relTime(a.coachingStart)
        : sleep; // Home School: no anchor
  const addMeal = (label: string, rawMin: number, dur: number) => {
    let m = rawMin;
    if (m < wake || m >= sleep) m = Math.min(Math.max(m, wake), sleep - dur); // keep within waking day
    m = Math.max(m, cursor); // never earlier than end of morning routine
    if (label === "Breakfast") {
      const latest = schoolStart - dur; // must finish before school/coaching starts
      if (m > latest) m = Math.max(Math.min(latest, cursor), wake); // slide back, not forward
    }
    m = Math.min(m, sleep - dur);
    m = Math.max(m, wake);
    fixed.push({ start: m, end: m + dur, type: "meal", label });
  };
  addMeal("Breakfast", relTime(a.breakfast), 20);
  addMeal("Lunch", relTime(a.lunch), 30);
  addMeal("Dinner", relTime(a.dinner), 30);

  // School / dummy school + commute
  if (a.educationType === "School" || (a.educationType === "Coaching" && a.coachingSubtype === "Dummy")) {
    const leave = relTime(a.leaveHome);
    const back = Math.max(relTime(a.backHome), leave + 30);
    const comm = Math.max(Number(a.commuteMinutes) || 0, 0);
    const travelOut = Math.max(leave - comm, wake);
    if (leave > travelOut) fixed.push({ start: travelOut, end: leave, type: "travel", label: "Travel to school/coaching" });
    if (Math.min(back, sleep) > leave) fixed.push({ start: leave, end: Math.min(back, sleep), type: "school", label: a.educationType === "School" ? "School" : "Dummy school" });
    if (back + comm <= sleep && back > leave) fixed.push({ start: back, end: Math.min(back + comm, sleep), type: "travel", label: "Travel home" });
  }
  // Residential coaching
  if (a.educationType === "Coaching" && a.coachingSubtype === "Residential") {
    const cs = relTime(a.coachingStart);
    const ce = Math.max(relTime(a.coachingEnd), cs + 30);
    if (Math.min(ce, sleep) > cs) fixed.push({ start: cs, end: Math.min(ce, sleep), type: "school", label: "Coaching" });
  }

  // Drop zero/negative-length artifacts and any block outside the waking day
  const valid = fixed.filter((b) => b.end > b.start && b.start >= 0 && b.start < sleep);
  valid.sort((x, y) => x.start - y.start);

  // Resolve overlaps with priority clipping: immovable blocks (school/travel)
  // win over meals, which win over the morning-routine chain. Each block keeps
  // only the time slices not already claimed by a higher-priority block.
  const rank: Record<SlotType, number> = {
    sleep: 0, school: 1, travel: 2, meal: 3, routine: 4, study: 5, break: 6,
  };
  const ordered = [...valid].sort(
    (x, y) => rank[x.type] - rank[y.type] || x.start - y.start
  );

  type IV = { s: number; e: number };
  let claimed: IV[] = []; // sorted, non-overlapping "already taken" intervals

  const claim = (s: number, e: number) => {
    if (e <= s) return;
    const out: IV[] = [];
    for (const c of claimed) {
      if (c.e <= s || c.s >= e) out.push(c);            // untouched
      else { if (c.s < s) out.push({ s: c.s, e: s }); } // keep left fragment
    }
    out.push({ s, e });
    out.sort((a, b) => a.s - b.s);
    const mergedIv: IV[] = [];
    for (const iv of out) {
      const last = mergedIv[mergedIv.length - 1];
      if (last && iv.s <= last.e) last.e = Math.max(last.e, iv.e);
      else mergedIv.push({ ...iv });
    }
    claimed = mergedIv;
  };
  const free = (s: number, e: number): boolean =>
    claimed.every((c) => c.e <= s || c.s >= e);

  const merged: Slot[] = [];
  for (const b of ordered) {
    if (free(b.start, b.end)) {
      merged.push({ ...b });
      claim(b.start, b.end);
    } else if (b.type === "meal") {
      // find the nearest free window of the same length. Breakfast searches
      // BACKWARD first (it must stay before school); other meals search forward.
      const dur = b.end - b.start;
      let placed = false;
      const limit = b.label === "Breakfast" ? Math.min(sleep, schoolStart) : sleep;
      const tryAt = (s: number) => {
        if (s >= wake && s + dur <= limit && free(s, s + dur)) {
          merged.push({ ...b, start: s, end: s + dur });
          claim(s, s + dur);
          placed = true;
          return true;
        }
        return false;
      };
      if (b.label === "Breakfast") {
        for (let s = b.start; !placed && s - dur >= wake; s -= 5) tryAt(s);
      }
      for (let s = b.start; !placed && s + dur <= limit; s += 5) tryAt(s);
      // last resort: drop a meal only when literally nowhere fits
    } else if (b.type === "routine") {
      // trim the routine's tail so it ends where the first claimed interval begins
      const cut = claimed.find((c) => c.s > b.start && c.s < b.end)?.s ?? b.start;
      if (cut - b.start >= 5) {
        merged.push({ ...b, end: cut });
        claim(b.start, cut);
      }
    }
    // school/travel fully overlapping another immovable block: drop silently
  }
  merged.sort((x, y) => x.start - y.start);

  // Walk from wake → sleep, filling gaps with study sessions
  const peak = PEAK_WINDOWS[a.peakProductivity] || PEAK_WINDOWS.Morning;
  const slots: Slot[] = [];
  if (wake > 0) slots.push({ start: 0, end: wake, type: "sleep", label: "Sleep" });
  const counter = { n: 0 };
  let cur = wake;
  for (const block of merged) {
    if (block.start < cur) continue; // fully overlapped — already handled by merge
    if (block.start > cur) fillStudySessions(slots, cur, block.start, a.studySessionMinutes, a.breakMinutes, counter, peak);
    slots.push(block);
    cur = Math.max(cur, block.end);
  }
  if (cur < sleep) fillStudySessions(slots, cur, sleep, a.studySessionMinutes, a.breakMinutes, counter, peak);
  if (sleep < 1439) slots.push({ start: sleep, end: 1439, type: "sleep", label: "Sleep" });
  // Safety net: clamp into [0, 1439], drop degenerate blocks, clip overlaps
  const out = slots
    .map((s) => ({ ...s, start: Math.max(0, s.start), end: Math.min(1439, s.end) }))
    .filter((s) => s.end > s.start)
    .sort((x, y) => x.start - y.start);
  for (let i = 1; i < out.length; i++) {
    if (out[i].start < out[i - 1].end) out[i].start = out[i - 1].end;
  }
  return out.filter((s) => s.end > s.start);
}

/* ================= Small UI helpers ================= */
const Pill = ({ active, onClick, children }: any) => (
  <button
    onClick={onClick}
    className={`px-4 py-2 rounded-full border text-sm font-medium transition ${
      active ? "gradient-emerald text-white border-transparent" : "bg-surface border-app text-app bg-surface-hover"
    }`}
  >
    {children}
  </button>
);

const PRESET_TASKS = [
  { label: "Brush & freshen up", minutes: 10, icon: Shower },
  { label: "Exercise / yoga", minutes: 30, icon: Dumbbell },
  { label: "Meditation / prayer", minutes: 15, icon: Sparkles },
  { label: "Reading / revision", minutes: 20, icon: BookOpen },
];

/* ================= Component ================= */
export default function OnboardingQuiz({ onComplete }: { onComplete: () => void }) {
  const { theme, toggleTheme } = useThemeLocal();
  const [step, setStep] = useState(0);
  const [dayPlan, setDayPlan] = useState<Slot[]>([]);
  const [saving, setSaving] = useState(false);

  const [a, setA] = useState<Answers>({
    educationType: "School", coachingSubtype: "Dummy", grade: "11", exams: ["JEE Main"],
    morningTasks: [{ label: "Brush & freshen up", minutes: 10 }],
    wakeTime: "06:00", sleepTime: "22:30", leaveHome: "08:00", backHome: "15:00",
    commuteMinutes: 30, coachingStart: "09:00", coachingEnd: "17:00",
    breakfast: "07:00", lunch: "13:00", dinner: "20:00", mealsPerDay: 3,
    peakProductivity: "Morning", studySessionMinutes: 50, breakMinutes: 10,
  });
  const set = (patch: Partial<Answers>) => setA((p) => ({ ...p, ...patch }));
  const [customTask, setCustomTask] = useState("");
  const [customMin, setCustomMin] = useState("20");

  const next = async () => {
    if (step === 5) {
      const plan = generateDayPlan(a);
      setDayPlan(plan);
      try {
        localStorage.setItem("vj_day_plan", JSON.stringify(plan));
        localStorage.setItem("vj_onboarding_data", JSON.stringify(a));
      } catch {}
    }
    if (step < 6) setStep(step + 1);
  };

  const finish = async () => {
    setSaving(true);
    try {
      await fetch(`${BACKEND_URL}/api/onboarding/save`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: 1, data: a, plan: dayPlan }),
      });
      toast.success("Day plan saved! (backend offline is OK — stored locally)");
    } catch {
      toast.info("Backend not reachable — plan kept in your browser.");
    }
    setSaving(false);
    onComplete();
  };

  const totalStudy = dayPlan.filter((s) => s.type === "study").reduce((n, s) => n + (s.end - s.start), 0);
  const nSessions = dayPlan.filter((s) => s.type === "study").length;
  const studyH = Math.floor(totalStudy / 60);
  const studyM = totalStudy % 60;

  return (
    <div className="min-h-screen bg-app text-app flex items-center justify-center p-4">
      <button onClick={toggleTheme} className="fixed top-4 right-4 z-50 p-2 rounded-lg border border-app bg-surface">
        {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
      </button>
      <div className="w-full max-w-2xl bg-surface border border-app rounded-2xl p-6 md:p-8 shadow-xl">
        {/* Progress */}
        <div className="flex items-center gap-2 mb-6">
          {Array.from({ length: 7 }).map((_, i) => (
            <div key={i} className={`h-1.5 flex-1 rounded-full ${i <= step ? "gradient-emerald" : "bg-[var(--border)]"}`} />
          ))}
        </div>
        <p className="text-muted text-xs mb-1">Step {step + 1} of 7</p>

        <AnimatePresence mode="wait">
          <motion.div
            key={step}
            initial={{ opacity: 0, x: 30 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -30 }}
            transition={{ duration: 0.25 }}
          >
            {/* STEP 0 — education type */}
            {step === 0 && (
              <Step title="How do you study?" icon={<School size={20} />}>
                <div className="flex flex-wrap gap-2">
                  {(["School", "Home School", "Coaching"] as const).map((t) => (
                    <Pill key={t} active={a.educationType === t} onClick={() => set({ educationType: t })}>{t}</Pill>
                  ))}
                </div>
                {a.educationType === "Coaching" && (
                  <div className="mt-4 flex gap-2">
                    {(["Dummy", "Residential"] as const).map((t) => (
                      <Pill key={t} active={a.coachingSubtype === t} onClick={() => set({ coachingSubtype: t })}>{t} Coaching</Pill>
                    ))}
                  </div>
                )}
              </Step>
            )}

            {/* STEP 1 — grade + exams */}
            {step === 1 && (
              <Step title="Grade & target exams" icon={<BookOpen size={20} />}>
                <select value={a.grade} onChange={(e) => set({ grade: e.target.value })} className="mb-4">
                  {["8","9","10","11","12","Drop year"].map((g) => <option key={g}>{g}</option>)}
                </select>
                <div className="flex flex-wrap gap-2">
                  {["JEE Main", "JEE Advanced", "NEET", "CBSE Boards", "SSC", "Olympiads"].map((ex) => (
                    <Pill key={ex} active={a.exams.includes(ex)}
                      onClick={() => set({ exams: a.exams.includes(ex) ? a.exams.filter((x) => x !== ex) : [...a.exams, ex] })}>
                      {ex}
                    </Pill>
                  ))}
                </div>
              </Step>
            )}

            {/* STEP 2 — morning routine */}
            {step === 2 && (
              <Step title="Morning routine" icon={<Sunrise size={20} />}>
                <div className="flex flex-wrap gap-2 mb-4">
                  {PRESET_TASKS.map((t) => {
                    const on = a.morningTasks.some((m) => m.label === t.label);
                    return (
                      <Pill key={t.label} active={on}
                        onClick={() => set({ morningTasks: on ? a.morningTasks.filter((m) => m.label !== t.label) : [...a.morningTasks, { label: t.label, minutes: t.minutes }] })}>
                        {t.label} ({t.minutes}m)
                      </Pill>
                    );
                  })}
                </div>
                <div className="flex gap-2">
                  <input placeholder="Custom task…" value={customTask} onChange={(e) => setCustomTask(e.target.value)} />
                  <input type="number" placeholder="min" value={customMin} onChange={(e) => setCustomMin(e.target.value)} className="!w-24" />
                  <button className="px-4 rounded-lg gradient-emerald text-white whitespace-nowrap"
                    onClick={() => { if (customTask.trim()) { set({ morningTasks: [...a.morningTasks, { label: customTask.trim(), minutes: Number(customMin) || 15 }] }); setCustomTask(""); } }}>
                    Add
                  </button>
                </div>
              </Step>
            )}

            {/* STEP 3 — schedule */}
            {step === 3 && (
              <Step title="Daily schedule" icon={<CalendarDays size={20} />}>
                <div className="grid grid-cols-2 gap-4">
                  <Field label="Wake up"><input type="time" value={a.wakeTime} onChange={(e) => set({ wakeTime: e.target.value })} /></Field>
                  <Field label="Sleep"><input type="time" value={a.sleepTime} onChange={(e) => set({ sleepTime: e.target.value })} /></Field>
                  {(a.educationType === "School" || a.coachingSubtype === "Dummy") && (<>
                    <Field label="Leave home"><input type="time" value={a.leaveHome} onChange={(e) => set({ leaveHome: e.target.value })} /></Field>
                    <Field label="Back home"><input type="time" value={a.backHome} onChange={(e) => set({ backHome: e.target.value })} /></Field>
                    <Field label="Commute (one way, min)"><input type="number" value={a.commuteMinutes} onChange={(e) => set({ commuteMinutes: Number(e.target.value) || 0 })} /></Field>
                  </>)}
                  {a.educationType === "Coaching" && a.coachingSubtype === "Residential" && (<>
                    <Field label="Coaching start"><input type="time" value={a.coachingStart} onChange={(e) => set({ coachingStart: e.target.value })} /></Field>
                    <Field label="Coaching end"><input type="time" value={a.coachingEnd} onChange={(e) => set({ coachingEnd: e.target.value })} /></Field>
                  </>)}
                </div>
              </Step>
            )}

            {/* STEP 4 — meals */}
            {step === 4 && (
              <Step title="Meals" icon={<Utensils size={20} />}>
                <div className="grid grid-cols-2 gap-4">
                  <Field label="Breakfast"><input type="time" value={a.breakfast} onChange={(e) => set({ breakfast: e.target.value })} /></Field>
                  <Field label="Lunch"><input type="time" value={a.lunch} onChange={(e) => set({ lunch: e.target.value })} /></Field>
                  <Field label="Dinner"><input type="time" value={a.dinner} onChange={(e) => set({ dinner: e.target.value })} /></Field>
                  <Field label="Meals per day"><input type="number" min={2} max={6} value={a.mealsPerDay} onChange={(e) => set({ mealsPerDay: Number(e.target.value) })} /></Field>
                </div>
              </Step>
            )}

            {/* STEP 5 — study preferences */}
            {step === 5 && (
              <Step title="Study preferences" icon={<Brain size={20} />}>
                <p className="text-muted text-sm mb-2">Peak productivity time</p>
                <div className="flex flex-wrap gap-2 mb-4">
                  {(["Morning", "Afternoon", "Evening", "Night"] as const).map((p) => (
                    <Pill key={p} active={a.peakProductivity === p} onClick={() => set({ peakProductivity: p })}>{p}</Pill>
                  ))}
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <Field label="Study session length (min)"><input type="number" value={a.studySessionMinutes} onChange={(e) => set({ studySessionMinutes: Number(e.target.value) || 50 })} /></Field>
                  <Field label="Break length (min)"><input type="number" value={a.breakMinutes} onChange={(e) => set({ breakMinutes: Number(e.target.value) || 10 })} /></Field>
                </div>
              </Step>
            )}

            {/* STEP 6 — generated plan */}
            {step === 6 && (
              <Step title="Your day plan ✨" icon={<Coffee size={20} />}>
                <div className="flex gap-3 mb-4 text-xs">
                  <span className="px-3 py-1 rounded-full bg-surface border border-app">{nSessions} study sessions</span>
                  <span className="px-3 py-1 rounded-full bg-surface border border-app">{studyH}h {studyM}m study/day</span>
                  <span className="px-3 py-1 rounded-full bg-surface border border-app">{dayPlan.length} blocks</span>
                </div>
                <div className="max-h-80 overflow-y-auto pr-2 space-y-1">
                  {dayPlan.map((s, i) => (
                    <div key={i} className="flex items-center gap-3 p-2 rounded-lg bg-[var(--bg)] border border-app text-sm">
                      <span className="font-mono text-xs text-muted w-24">{m2t(s.start)}–{m2t(s.end)}</span>
                      <TypeIcon type={s.type} />
                      <span className="capitalize">{s.label}</span>
                    </div>
                  ))}
                </div>
              </Step>
            )}
          </motion.div>
        </AnimatePresence>

        {/* Nav buttons */}
        <div className="flex justify-between mt-6">
          <button disabled={step === 0} onClick={() => setStep(step - 1)}
            className="flex items-center gap-1 px-4 py-2 rounded-lg border border-app text-muted disabled:opacity-30">
            <ChevronLeft size={16} /> Back
          </button>
          {step < 6 ? (
            <button onClick={next} className="flex items-center gap-1 px-5 py-2 rounded-lg gradient-emerald text-white font-semibold">
              Next <ChevronRight size={16} />
            </button>
          ) : (
            <button onClick={finish} disabled={saving} className="px-5 py-2 rounded-lg gradient-emerald text-white font-semibold disabled:opacity-60">
              {saving ? "Saving…" : "Start Learning 🚀"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

/* Reuse the theme hook without circular import issues */
function useThemeLocal() {
  const [theme, setTheme] = useState<"dark" | "light">(() =>
    localStorage.getItem("vj_theme") === "light" ? "light" : "dark");
  const toggleTheme = () => {
    const root = document.documentElement;
    const nextT = theme === "dark" ? "light" : "dark";
    root.classList.remove("dark", "light");
    root.classList.add(nextT);
    localStorage.setItem("vj_theme", nextT);
    setTheme(nextT);
  };
  return { theme, toggleTheme };
}

const Step = ({ title, icon, children }: any) => (
  <div>
    <h2 className="text-xl font-bold mb-4 flex items-center gap-2">{icon}{title}</h2>
    {children}
  </div>
);

const Field = ({ label, children }: any) => (
  <label className="block">
    <span className="text-xs text-muted">{label}</span>
    {children}
  </label>
);

const TypeIcon = ({ type }: { type: SlotType }) => {
  const map: Record<SlotType, any> = {
    study: BookOpen, meal: Utensils, school: School, routine: Sunrise,
    break: Coffee, sleep: Moon, travel: Bus,
  };
  const I = map[type];
  const colors: Record<SlotType, string> = {
    study: "text-[var(--primary)]", meal: "text-orange-400", school: "text-sky-400",
    routine: "text-purple-400", break: "text-yellow-400", sleep: "text-indigo-400", travel: "text-pink-400",
  };
  return <I size={16} className={colors[type]} />;
};
