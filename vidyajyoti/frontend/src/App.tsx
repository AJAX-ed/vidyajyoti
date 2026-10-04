import { useEffect, useState } from "react";
import { Toaster } from "sonner";
import LoginPage from "./views/LoginPage";
import OnboardingQuiz from "./views/OnboardingQuiz";
import Dashboard from "./views/Dashboard";

export type AppState = "login" | "onboarding" | "app";

/* ---------- Theme hook: toggles dark/light on <html>, persists to localStorage ---------- */
export function useTheme() {
  const [theme, setTheme] = useState<"dark" | "light">(() => {
    const saved = localStorage.getItem("vj_theme");
    return saved === "light" ? "light" : "dark";
  });

  useEffect(() => {
    const root = document.documentElement;
    root.classList.remove("dark", "light");
    root.classList.add(theme);
    localStorage.setItem("vj_theme", theme);
  }, [theme]);

  const toggleTheme = () => setTheme((t) => (t === "dark" ? "light" : "dark"));
  return { theme, toggleTheme };
}

export default function App() {
  const [appState, setAppState] = useState<AppState>("login");

  /* Restore session on startup */
  useEffect(() => {
    const token = localStorage.getItem("vj_token");
    const name = localStorage.getItem("vj_user_name");
    if (token && name) {
      setAppState(localStorage.getItem("vj_onboarded") === "true" ? "app" : "onboarding");
    } else {
      setAppState("login");
    }
  }, []);

  const handleLogin = (name?: string) => {
    localStorage.setItem("vj_token", "fake-login");
    localStorage.setItem("vj_user_name", name || "Student");
    setAppState("onboarding");
  };

  const handleOnboardingComplete = () => {
    localStorage.setItem("vj_onboarded", "true");
    setAppState("app");
  };

  const handleLogout = () => {
    Object.keys(localStorage)
      .filter((k) => k.startsWith("vj_"))
      .forEach((k) => localStorage.removeItem(k));
    setAppState("login");
  };

  return (
    <>
      <Toaster position="top-center" richColors />
      {appState === "login" && <LoginPage onLogin={handleLogin} />}
      {appState === "onboarding" && <OnboardingQuiz onComplete={handleOnboardingComplete} />}
      {appState === "app" && <Dashboard onLogout={handleLogout} />}
    </>
  );
}
