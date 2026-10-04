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

function fillStudySessions(
  slots: Slot[], start: number, end: number,
  sessionMin: number, breakMin: number, counter: { n: number }
) {
  let gap = end - start;
  if (gap < 20) {
    if (gap >= 5) slots.push({ start, end, type: "break", label: "Short break / free time" });
    return;
  }
  let cur = start;
  while (end - cur >= sessionMin) {
    counter.n += 1;
    slots.push({ start: cur, end: cur + sessionMin, type: "study", label: `Study session ${counter.n}` });
    cur += sessionMin;
    // add a break only if a full session still fits after it
    if (end - cur >= breakMin + sessionMin) {
      slots.push({ start: cur, end: cur + breakMin, type: "break", label: "Break" });
      cur += breakMin;
    } else if (end - cur > 0 && end - cur < sessionMin) {
      break;
    }
  }
  const leftover = end - cur;
  if (leftover >= 5) slots.push({ start: cur, end, type: "break", label: "Free time" });
  void gap;
}

function generateDayPlan(a: Answers): Slot[] {
  const wake = t2m(a.wakeTime);
  const sleep = t2m(a.sleepTime) <= wake ? t2m(a.sleepTime) + 1440 : t2m(a.sleepTime);
  const fixed: Slot[] = [];

  // Sleep before wake (00:00 → wake)
  fixed.push({ start: 0, end: wake, type: "sleep", label: "Sleep" });

  // Morning routine starting at wake time
  let cursor = wake;
  for (const task of a.morningTasks) {
    fixed.push({ start: cursor, end: cursor + task.minutes, type: "routine", label: task.label });
    cursor += task.minutes;
  }

  // Meals
  const breakfast = t2m(a.breakfast);
  if (breakfast >= wake) fixed.push({ start: breakfast, end: breakfast + 20, type: "meal", label: "Breakfast" });
  const lunch = t2m(a.lunch);
  fixed.push({ start: lunch, end: lunch + 30, type: "meal", label: "Lunch" });
  const dinner = t2m(a.dinner);
  fixed.push({ start: dinner, end: dinner + 30, type: "meal", label: "Dinner" });

  // School / dummy school + commute
  if (a.educationType === "School" || (a.educationType === "Coaching" && a.coachingSubtype === "Dummy")) {
    const leave = t2m(a.leaveHome);
    const back = t2m(a.backHome);
    fixed.push({ start: leave - a.commuteMinutes, end: leave, type: "travel", label: "Travel to school/coaching" });
    fixed.push({ start: leave, end: back, type: "school", label: a.educationType === "School" ? "School" : "Dummy school" });
    fixed.push({ start: back, end: back + a.commuteMinutes, type: "travel", label: "Travel home" });
  }
  // Residential coaching
  if (a.educationType === "Coaching" && a.coachingSubtype === "Residential") {
    fixed.push({ start: t2m(a.coachingStart), end: t2m(a.coachingEnd), type: "school", label: "Coaching" });
  }

  fixed.sort((x, y) => x.start - y.start);

  // Walk from wake → sleep, filling gaps with study sessions
  const slots: Slot[] = [{ start: 0, end: wake, type: "sleep", label: "Sleep" }];
  const counter = { n: 0 };
  let cur = wake;
  for (const block of fixed.slice(1)) {
    if (block.start > cur) fillStudySessions(slots, cur, block.start, a.studySessionMinutes, a.breakMinutes, counter);
    if (block.start >= cur) slots.push(block);
    cur = Math.max(cur, block.end);
  }
  if (cur < sleep) fillStudySessions(slots, cur, sleep, a.studySessionMinutes, a.breakMinutes, counter);
  slots.push({ start: sleep, end: 1439, type: "sleep", label: "Sleep" });
  return slots.sort((x, y) => x.start - y.start);
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
                  <span className="px-3 py-1 rounded-full bg-surface border border-app">{Math.round(totalStudy / 6)} h {totalStudy % 60} m study/day</span>
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
