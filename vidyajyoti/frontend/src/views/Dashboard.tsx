import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import {
  GraduationCap, Moon, Sun, LogOut, Coins, Flame, Star, LayoutDashboard, Swords,
  HelpCircle, Trophy, BookOpen, CalendarDays, Target, BarChart3, Users, Settings,
  Bell, Library, Wallet, Coffee, Utensils, School, Sunrise, Bus, ChevronRight,
} from "lucide-react";
import type { Slot, SlotType, WeeklyGoal, MonthlyGoal, TopicGoal } from "./OnboardingQuiz";

const BACKEND_URL = import.meta.env.VITE_BACKEND_URL || "http://localhost:8000";

function todayISO(): string { return new Date().toISOString().slice(0, 10); }

/* Local fallback daily variation — mirrors the ML service's
   daily-variation-v1 so a new day still looks fresh even fully offline. */
function localVary(base: Slot[], seed: string, focus: TopicGoal[]): Slot[] {
  let h = 2166136261;
  for (const c of seed) { h ^= c.charCodeAt(0); h = Math.imul(h, 16777619); }
  const rnd = () => { h = Math.imul(h ^ (h >>> 15), 2246822507); h = Math.imul(h ^ (h >>> 13), 3266489909); return ((h ^= h >>> 16) >>> 0) / 4294967296; };
  const topics = focus.length ? focus : [{ subject: "Revision", topic: "Weekly recap" }];
  const studies = base.filter((s) => s.type === "study");
  if (!studies.length) return base;
  const offset = Math.floor(rnd() * topics.length);
  const labels = studies.map((_, k) => { const t = topics[(offset + k) % topics.length]; return `${t.subject}: ${t.topic}`; });
  const out = base.map((s) => ({ ...s }));
  let si = 0;
  for (const s of out) {
    if (s.type !== "study") continue;
    const dur = s.end - s.start;
    s.label = labels[si % labels.length];
    if (dur >= 30) s.end = s.start + Math.min(dur, [45, 50, 60][Math.floor(rnd() * 3)]);
    si++;
  }
  if (si >= 3) {
    const idx = out.map((s, i) => s.type === "study" ? i : -1).filter((i) => i >= 0);
    out[idx[idx.length - 1]].label = `Quiz time 🎯 (${out[idx[idx.length - 1]].label})`;
    out[idx[Math.floor(idx.length / 2)]].label = `Revision: ${out[idx[Math.floor(idx.length / 2)]].label}`;
  }
  return out.filter((s) => s.end > s.start);
}

/* ---------- Theme (kept local so views stay independent) ---------- */
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

const NAV_ITEMS = [
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard },
  { id: "battlegrounds", label: "Battlegrounds", icon: Swords },
  { id: "doubts", label: "Ask a Doubt", icon: HelpCircle },
  { id: "leaderboard", label: "Leaderboard", icon: Trophy },
  { id: "syllabus", label: "Syllabus Tracker", icon: BookOpen },
  { id: "dayplan", label: "Day Plan", icon: CalendarDays },
  { id: "exams", label: "Exam Goals", icon: Target },
  { id: "analytics", label: "Analytics", icon: BarChart3 },
  { id: "friends", label: "Friends", icon: Users },
  { id: "library", label: "Library", icon: Library },
  { id: "points", label: "Points & Coins", icon: Wallet },
  { id: "notifications", label: "Notifications", icon: Bell },
  { id: "settings", label: "Settings", icon: Settings },
];

function m2t(minutes: number): string {
  const mm = ((minutes % 1440) + 1440) % 1440;
  return `${String(Math.floor(mm / 60)).padStart(2, "0")}:${String(mm % 60).padStart(2, "0")}`;
}

const SLOT_ICON: Record<SlotType, any> = {
  study: BookOpen, meal: Utensils, school: School, routine: Sunrise,
  break: Coffee, sleep: Moon, travel: Bus,
};

export default function Dashboard({ onLogout }: { onLogout: () => void }) {
  const { theme, toggleTheme } = useThemeLocal();
  const [view, setView] = useState("dashboard");
  const userName = localStorage.getItem("vj_user_name") || "Student";
  const [plan, setPlan] = useState<Slot[]>(() => {
    try {
      const stored = JSON.parse(localStorage.getItem("vj_day_plan") || "[]") as Slot[];
      // Stale plan from an earlier day? Re-vary it locally right now so the
      // student sees a fresh timetable the moment the date rolls over —
      // then the backend/ML refresh below upgrades it with real goals.
      const savedDate = localStorage.getItem("vj_plan_date") || "";
      if (savedDate !== todayISO() && stored.length) {
        let focus: TopicGoal[] = [];
        try {
          const wk = JSON.parse(localStorage.getItem("vj_weekly_goals") || "[]") as WeeklyGoal[];
          focus = wk.length ? wk[0].topics.slice(0, 4) : [];
        } catch {}
        return localVary(stored, `1:${todayISO()}`, focus);
      }
      return stored;
    } catch { return []; }
  });
  const [weeklyGoals] = useState<WeeklyGoal[]>(() => { try { return JSON.parse(localStorage.getItem("vj_weekly_goals") || "[]"); } catch { return []; } });
  const [monthlyGoals] = useState<MonthlyGoal[]>(() => { try { return JSON.parse(localStorage.getItem("vj_monthly_goals") || "[]"); } catch { return []; } });
  const [goalsSummary] = useState<string>(() => localStorage.getItem("vj_goals_summary") || "");
  const [dayGenerated, setDayGenerated] = useState(false);

  /* NEW-DAY LOGIN: ask the backend for today's personalized timetable.
     The self-hosted ML service rotates topics + varies session lengths/order
     per calendar date; anchors (wake/school/meals) never move. Falls back to
     the locally varied plan when the backend is offline. */
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const ctrl = new AbortController();
        const timer = setTimeout(() => ctrl.abort(), 6000);
        const res = await fetch(`${BACKEND_URL}/api/onboarding/1/today`, { signal: ctrl.signal });
        clearTimeout(timer);
        if (!res.ok || !alive) return;
        const ct = res.headers.get("content-type") || "";
        if (!ct.includes("json")) return;
        const d = await res.json();
        if (!Array.isArray(d.plan) || !d.plan.length) return;
        setPlan(d.plan);
        if (Array.isArray(d.weekly_goals) && d.weekly_goals.length) {
          localStorage.setItem("vj_weekly_goals", JSON.stringify(d.weekly_goals));
        }
        if (Array.isArray(d.monthly_goals) && d.monthly_goals.length) {
          localStorage.setItem("vj_monthly_goals", JSON.stringify(d.monthly_goals));
        }
        localStorage.setItem("vj_day_plan", JSON.stringify(d.plan));
        localStorage.setItem("vj_plan_date", d.date || todayISO());
        setDayGenerated(!!d.generated);
      } catch { /* backend offline → local varied plan stands */ }
    })();
    return () => { alive = false; };
  }, []);

  const studyMinutes = plan.filter((s) => s.type === "study").reduce((n, s) => n + (s.end - s.start), 0);
  const nowMin = new Date().getHours() * 60 + new Date().getMinutes();
  const currentSlot = plan.find((s) => nowMin >= s.start && nowMin < s.end);
  const today = new Date().toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  const greeting = nowMin < 12 * 60 ? "Good morning" : nowMin < 17 * 60 ? "Good afternoon" : "Good evening";

  return (
    <div className="min-h-screen bg-app text-app flex">
      {/* Sidebar (desktop) */}
      <aside className="hidden md:flex flex-col w-60 border-r border-app bg-surface p-4 gap-1">
        <div className="flex items-center gap-2 mb-4 px-2">
          <GraduationCap size={24} className="text-[var(--primary)]" />
          <span className="font-bold text-lg text-gradient-emerald">VidyaJyoti</span>
        </div>
        {NAV_ITEMS.map((item) => (
          <button key={item.id} onClick={() => setView(item.id)}
            className={`flex items-center gap-3 px-3 py-2 rounded-lg text-sm text-left ${
              view === item.id ? "gradient-emerald text-white" : "text-muted bg-surface-hover"}`}>
            <item.icon size={16} /> {item.label}
          </button>
        ))}
        <div className="mt-auto" />
        <button onClick={onLogout} className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-red-400 bg-surface-hover">
          <LogOut size={16} /> Logout
        </button>
      </aside>

      <div className="flex-1 flex flex-col min-w-0">
        {/* Header */}
        <header className="flex items-center gap-3 p-4 border-b border-app bg-surface">
          <GraduationCap size={22} className="md:hidden text-[var(--primary)]" />
          <div className="flex items-center gap-2 ml-auto">
            <span className="flex items-center gap-1 px-3 py-1 rounded-full text-xs border border-app bg-[var(--bg)]"><Star size={12} className="text-yellow-400" /> 1,250 pts</span>
            <span className="flex items-center gap-1 px-3 py-1 rounded-full text-xs border border-app bg-[var(--bg)]"><Coins size={12} className="text-amber-400" /> 320</span>
            <span className="flex items-center gap-1 px-3 py-1 rounded-full text-xs border border-app bg-[var(--bg)]"><Flame size={12} className="text-orange-500" /> 7-day streak</span>
            <button onClick={toggleTheme} className="p-2 rounded-lg border border-app" aria-label="Toggle theme">
              {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
            </button>
            <span className="text-sm font-medium hidden sm:inline">Hi, {userName}</span>
            <button onClick={onLogout} className="md:hidden p-2 rounded-lg border border-app text-red-400"><LogOut size={16} /></button>
          </div>
        </header>

        {/* Main */}
        <main className="flex-1 p-4 md:p-6 overflow-y-auto pb-20 md:pb-6">
          {view === "dashboard" ? (
            <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="space-y-6 max-w-4xl">
              {/* Hero */}
              <div className="gradient-emerald rounded-2xl p-6 text-white">
                <h1 className="text-2xl font-bold">{greeting}, {userName} 🪔</h1>
                <p className="opacity-90 text-sm mb-4">{today}</p>
                <div className="flex flex-wrap gap-3 text-sm">
                  <span className="bg-white/15 rounded-lg px-3 py-1.5">{Math.floor(studyMinutes / 60)}h {studyMinutes % 60}m planned study</span>
                  <span className="bg-white/15 rounded-lg px-3 py-1.5">{plan.filter((s) => s.type === "study").length} sessions today</span>
                  <span className="bg-white/15 rounded-lg px-3 py-1.5">
                    Now: {currentSlot ? currentSlot.label : plan.length === 0 ? "Complete onboarding to see your plan" : "Free time"}
                  </span>
                  {dayGenerated && (
                    <span className="bg-white/25 rounded-lg px-3 py-1.5 font-semibold">✨ New timetable generated for today!</span>
                  )}
                </div>
              </div>

              {/* Quick actions */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                {[
                  { label: "Start Battle", icon: Swords, go: "battlegrounds" },
                  { label: "Ask Doubt", icon: HelpCircle, go: "doubts" },
                  { label: "View Plan", icon: CalendarDays, go: "dayplan" },
                  { label: "Leaderboard", icon: Trophy, go: "leaderboard" },
                ].map((q) => (
                  <button key={q.label} onClick={() => setView(q.go)}
                    className="bg-surface border border-app rounded-xl p-4 flex items-center gap-3 bg-surface-hover text-sm font-medium">
                    <q.icon size={18} className="text-[var(--primary)]" /> {q.label} <ChevronRight size={14} className="ml-auto text-muted" />
                  </button>
                ))}
              </div>

              {/* Weekly & monthly syllabus goals (ML pacing planner) */}
              {(weeklyGoals.length > 0 || monthlyGoals.length > 0) && (
                <div className="grid md:grid-cols-2 gap-4">
                  <div className="bg-surface border border-app rounded-2xl p-5">
                    <h2 className="font-bold mb-1 flex items-center gap-2"><Target size={16} className="text-[var(--primary)]" /> This Week's Goal</h2>
                    {goalsSummary && <p className="text-xs text-muted mb-3">{goalsSummary}</p>}
                    <div className="flex flex-wrap gap-2">
                      {(weeklyGoals[0]?.topics || []).map((t, i) => (
                        <span key={i} className="px-3 py-1 rounded-full text-xs gradient-emerald text-white">{t.subject}: {t.topic}</span>
                      ))}
                    </div>
                    {weeklyGoals.length > 1 && (
                      <p className="text-xs text-muted mt-3">Weeks 2–{weeklyGoals.length} queued — paced to finish by Dec 31 without burning you out.</p>
                    )}
                  </div>
                  <div className="bg-surface border border-app rounded-2xl p-5">
                    <h2 className="font-bold mb-3 flex items-center gap-2"><CalendarDays size={16} className="text-[var(--primary)]" /> Monthly Milestones</h2>
                    <div className="space-y-2 max-h-40 overflow-y-auto pr-1">
                      {monthlyGoals.map((m, i) => (
                        <div key={i} className="flex items-center justify-between text-sm p-2 rounded-lg bg-[var(--bg)] border border-app">
                          <span className="font-medium">{m.month}</span>
                          <span className="text-xs text-muted">{m.milestone}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              )}

              {/* Today's plan */}
              <div className="bg-surface border border-app rounded-2xl p-5">
                <h2 className="font-bold mb-4">Today's Plan <span className="text-xs font-normal text-muted">(regenerated fresh every day)</span></h2>
                {plan.length === 0 ? (
                  <p className="text-muted text-sm">No plan yet — redo onboarding to generate one.</p>
                ) : (
                  <div className="space-y-1">
                    {plan.map((s, i) => {
                      const I = SLOT_ICON[s.type];
                      const active = nowMin >= s.start && nowMin < s.end;
                      return (
                        <div key={i} className={`flex items-center gap-3 p-2 rounded-lg text-sm border ${active ? "border-[var(--primary)] bg-[var(--surface-hover)]" : "border-transparent"}`}>
                          <span className="font-mono text-xs text-muted w-24">{m2t(s.start)}–{m2t(s.end)}</span>
                          <I size={15} className="text-[var(--primary)] shrink-0" />
                          <span>{s.label}</span>
                          {active && <span className="ml-auto text-xs gradient-emerald text-white px-2 py-0.5 rounded-full">now</span>}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </motion.div>
          ) : (
            /* Placeholder for future modules */
            <div className="bg-surface border border-app rounded-2xl p-10 max-w-2xl mx-auto text-center">
              <LayoutDashboard size={36} className="mx-auto mb-3 text-[var(--primary)]" />
              <h2 className="text-xl font-bold mb-2">{NAV_ITEMS.find((n) => n.id === view)?.label}</h2>
              <p className="text-muted text-sm">
                This module is being built with <b>FastAPI + PostgreSQL</b>. Coming soon — powered by our own self-hosted AI models (no external AI APIs).
              </p>
            </div>
          )}
        </main>

        {/* Bottom nav (mobile) — first 8 items, horizontally scrollable */}
        <nav className="md:hidden fixed bottom-0 inset-x-0 bg-surface border-t border-app flex overflow-x-auto">
          {NAV_ITEMS.slice(0, 8).map((item) => (
            <button key={item.id} onClick={() => setView(item.id)}
              className={`flex flex-col items-center gap-0.5 px-4 py-2 text-[10px] whitespace-nowrap min-w-[64px] ${view === item.id ? "text-[var(--primary)]" : "text-muted"}`}>
              <item.icon size={18} /> {item.label.split(" ")[0]}
            </button>
          ))}
        </nav>
      </div>
    </div>
  );
}
