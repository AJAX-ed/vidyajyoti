import { useState } from "react";
import { motion } from "framer-motion";
import { GraduationCap, Target, CalendarClock, Brain, Moon, Sun, Sparkles } from "lucide-react";
import { useTheme } from "../App";

const FEATURES = [
  { icon: CalendarClock, title: "Smart Day Plans", desc: "Timelines built around your real routine." },
  { icon: Target, title: "Exam Focused", desc: "JEE · NEET · CBSE · SSC — all in one place." },
  { icon: Brain, title: "Own AI Models", desc: "Self-hosted personalisation. No external AI APIs." },
  { icon: Sparkles, title: "Battlegrounds", desc: "Gamified quizzes with points & coins." },
];

export default function LoginPage({ onLogin }: { onLogin: (name?: string) => void }) {
  const { theme, toggleTheme } = useTheme();
  const [name, setName] = useState("");

  return (
    <div className="min-h-screen bg-app text-app flex">
      {/* Theme toggle visible on all screens */}
      <button
        onClick={toggleTheme}
        className="fixed top-4 right-4 z-50 p-2 rounded-lg border border-app bg-surface"
        aria-label="Toggle theme"
      >
        {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
      </button>

      {/* LEFT: branding (desktop only) */}
      <div className="hidden lg:flex flex-col justify-center flex-1 p-12 gradient-emerald text-white">
        <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.6 }}>
          <div className="flex items-center gap-3 mb-6">
            <GraduationCap size={40} />
            <h1 className="text-4xl font-bold">VidyaJyoti</h1>
          </div>
          <p className="text-xl opacity-90 mb-10 max-w-md">
            The light of knowledge — plan your day, master your exam, level up every battle.
          </p>
          <div className="grid grid-cols-2 gap-4 max-w-lg">
            {FEATURES.map((f) => (
              <div key={f.title} className="bg-white/10 backdrop-blur rounded-xl p-4 border border-white/20">
                <f.icon className="mb-2" size={22} />
                <h3 className="font-semibold">{f.title}</h3>
                <p className="text-sm opacity-80">{f.desc}</p>
              </div>
            ))}
          </div>
        </motion.div>
      </div>

      {/* RIGHT: login card (always visible; only card on mobile) */}
      <div className="flex-1 flex items-center justify-center p-6">
        <motion.div
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          className="w-full max-w-md bg-surface border border-app rounded-2xl p-8 shadow-xl"
        >
          <div className="flex items-center gap-2 mb-2 lg:hidden">
            <GraduationCap size={28} className="text-[var(--primary)]" />
            <span className="text-2xl font-bold text-gradient-emerald">VidyaJyoti</span>
          </div>
          <h2 className="text-2xl font-bold mb-1">Welcome back 🪔</h2>
          <p className="text-muted text-sm mb-6">
            Start your study journey — no signup needed for this demo.
          </p>
          <input
            placeholder="Your name (optional)"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="mb-4"
          />
          <button
            onClick={() => onLogin(name.trim() || undefined)}
            className="w-full gradient-emerald text-white font-semibold py-3 rounded-xl hover:opacity-90"
          >
            Login
          </button>
        </motion.div>
      </div>
    </div>
  );
}
