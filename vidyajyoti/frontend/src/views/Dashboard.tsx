import { useState } from 'react';
import { 
  BookOpen, Trophy, Target, Users, Calendar, MessageSquare, 
  BarChart3, Settings, LogOut, Sun, Moon, Home, GraduationCap,
  Brain, Award, Clock, HelpCircle
} from 'lucide-react';

interface DashboardProps {
  onLogout: () => void;
}

const navItems = [
  { id: 'dashboard', label: 'Dashboard', icon: Home },
  { id: 'study-plan', label: 'Study Plan', icon: Calendar },
  { id: 'subjects', label: 'Subjects', icon: BookOpen },
  { id: 'battlegrounds', label: 'Battlegrounds', icon: Trophy },
  { id: 'doubts', label: 'Doubts', icon: MessageSquare },
  { id: 'performance', label: 'Performance', icon: BarChart3 },
  { id: 'points', label: 'Points', icon: Award },
  { id: 'streak', label: 'Streak', icon: Target },
  { id: 'community', label: 'Community', icon: Users },
  { id: 'ai-tutor', label: 'AI Tutor', icon: Brain },
  { id: 'exams', label: 'Exams', icon: GraduationCap },
  { id: 'schedule', label: 'Schedule', icon: Clock },
  { id: 'help', label: 'Help', icon: HelpCircle },
  { id: 'settings', label: 'Settings', icon: Settings },
];

export default function Dashboard({ onLogout }: DashboardProps) {
  const [activeView, setActiveView] = useState('dashboard');

  // Theme toggle
  const toggleTheme = () => {
    const html = document.documentElement;
    if (html.classList.contains('dark')) {
      html.classList.remove('dark');
      html.classList.add('light');
      localStorage.setItem('vj_theme', 'light');
    } else {
      html.classList.remove('light');
      html.classList.add('dark');
      localStorage.setItem('vj_theme', 'dark');
    }
  };

  const userName = localStorage.getItem('vj_user_name') || 'Student';
  const today = new Date().toLocaleDateString('en-IN', {
    weekday: 'long',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  });

  // Mock day plan from onboarding (in real app, fetch from backend)
  const dayPlan = [
    { start: '06:00', end: '06:15', type: 'routine', label: 'Wake up & freshen up' },
    { start: '06:15', end: '06:30', type: 'routine', label: 'Exercise' },
    { start: '06:30', end: '07:15', type: 'study', label: 'Study session 1' },
    { start: '07:15', end: '07:35', type: 'meal', label: 'Breakfast' },
    { start: '07:35', end: '08:05', type: 'travel', label: 'Commute to school' },
    { start: '08:05', end: '15:05', type: 'school', label: 'School' },
    { start: '15:05', end: '15:35', type: 'travel', label: 'Commute home' },
    { start: '15:35', end: '16:05', type: 'meal', label: 'Lunch' },
    { start: '16:05', end: '16:50', type: 'study', label: 'Study session 2' },
    { start: '16:50', end: '17:00', type: 'break', label: 'Break' },
    { start: '17:00', end: '17:45', type: 'study', label: 'Study session 3' },
    { start: '17:45', end: '18:00', type: 'break', label: 'Free time' },
    { start: '20:30', end: '21:00', type: 'meal', label: 'Dinner' },
    { start: '23:00', end: '23:59', type: 'sleep', label: 'Sleep' },
  ];

  const getSlotIcon = (type: string) => {
    switch (type) {
      case 'study': return <BookOpen className="w-4 h-4 text-primary" />;
      case 'meal': return <Users className="w-4 h-4 text-accent" />;
      case 'school': return <GraduationCap className="w-4 h-4 text-primary" />;
      case 'routine': return <Sun className="w-4 h-4 text-accent" />;
      case 'break': return <Moon className="w-4 h-4 text-muted" />;
      case 'sleep': return <Moon className="w-4 h-4 text-muted" />;
      case 'travel': return <Clock className="w-4 h-4 text-accent" />;
      default: return <Target className="w-4 h-4" />;
    }
  };

  const renderMainContent = () => {
    if (activeView !== 'dashboard') {
      return (
        <div className="bg-surface p-8 rounded-2xl border-app border text-center">
          <Brain className="w-16 h-16 text-primary mx-auto mb-4" />
          <h2 className="text-2xl font-bold text-app mb-2 capitalize">{activeView.replace('-', ' ')}</h2>
          <p className="text-muted mb-4">This module is being built with FastAPI + PostgreSQL</p>
          <p className="text-sm text-muted">Coming soon with self-hosted AI features!</p>
        </div>
      );
    }

    return (
      <>
        {/* Hero Card */}
        <div className="gradient-emerald rounded-2xl p-6 text-white mb-6">
          <h1 className="text-2xl font-bold mb-2">Hello, {userName}! 👋</h1>
          <p className="opacity-90 mb-4">{today}</p>
          <div className="grid grid-cols-3 gap-4">
            <div>
              <p className="text-sm opacity-75">Points</p>
              <p className="text-2xl font-bold">1,250</p>
            </div>
            <div>
              <p className="text-sm opacity-75">Coins</p>
              <p className="text-2xl font-bold">450</p>
            </div>
            <div>
              <p className="text-sm opacity-75">Streak</p>
              <p className="text-2xl font-bold">7 days 🔥</p>
            </div>
          </div>
        </div>

        {/* Quick Actions */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          {[
            { label: 'Start Quiz', icon: Trophy, color: 'text-accent' },
            { label: 'Ask Doubt', icon: MessageSquare, color: 'text-primary' },
            { label: 'View Plan', icon: Calendar, color: 'text-accent' },
            { label: 'Practice', icon: Brain, color: 'text-primary' },
          ].map((action) => (
            <button
              key={action.label}
              className="bg-surface p-4 rounded-xl border-app border hover:bg-surface-hover transition-colors text-left"
            >
              <action.icon className={`w-6 h-6 ${action.color} mb-2`} />
              <p className="text-app font-medium">{action.label}</p>
            </button>
          ))}
        </div>

        {/* Today's Plan */}
        <div className="bg-surface rounded-2xl border-app border p-6">
          <h2 className="text-xl font-bold text-app mb-4">Today's Plan</h2>
          <div className="space-y-2 max-h-96 overflow-y-auto">
            {dayPlan.map((slot, idx) => (
              <div key={idx} className="flex items-center gap-3 p-3 bg-app rounded-xl">
                {getSlotIcon(slot.type)}
                <div className="flex-1">
                  <p className="text-app font-medium text-sm">{slot.label}</p>
                  <p className="text-muted text-xs">{slot.start} – {slot.end}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </>
    );
  };

  return (
    <div className="min-h-screen bg-app flex">
      {/* Sidebar (desktop) */}
      <aside className="hidden lg:flex w-64 bg-surface border-r border-app flex-col">
        <div className="p-6 border-b border-app">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 gradient-emerald rounded-xl flex items-center justify-center">
              <BookOpen className="w-6 h-6 text-white" />
            </div>
            <span className="text-lg font-bold text-gradient-emerald">VidyaJyoti</span>
          </div>
        </div>

        <nav className="flex-1 p-4 overflow-y-auto">
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                onClick={() => setActiveView(item.id)}
                className={`w-full flex items-center gap-3 px-4 py-3 rounded-xl mb-1 transition-colors ${
                  activeView === item.id
                    ? 'bg-surface-hover text-primary'
                    : 'text-muted hover:text-app hover:bg-surface-hover'
                }`}
              >
                <Icon className="w-5 h-5" />
                <span className="text-sm font-medium">{item.label}</span>
              </button>
            );
          })}
        </nav>

        <div className="p-4 border-t border-app">
          <button
            onClick={onLogout}
            className="w-full flex items-center gap-3 px-4 py-3 rounded-xl text-muted hover:text-app hover:bg-surface-hover transition-colors"
          >
            <LogOut className="w-5 h-5" />
            <span className="text-sm font-medium">Logout</span>
          </button>
        </div>
      </aside>

      {/* Main content */}
      <div className="flex-1 flex flex-col">
        {/* Header */}
        <header className="bg-surface border-b border-app p-4 flex justify-between items-center">
          <div className="lg:hidden flex items-center gap-3">
            <div className="w-8 h-8 gradient-emerald rounded-xl flex items-center justify-center">
              <BookOpen className="w-5 h-5 text-white" />
            </div>
            <span className="font-bold text-gradient-emerald">VidyaJyoti</span>
          </div>

          <div className="hidden lg:block">
            <h1 className="text-lg font-semibold text-app capitalize">
              {activeView.replace('-', ' ')}
            </h1>
          </div>

          <div className="flex items-center gap-4">
            {/* Stats pills */}
            <div className="hidden md:flex items-center gap-2">
              <div className="px-3 py-1 bg-app rounded-full text-xs font-medium text-primary">
                🪙 450 coins
              </div>
              <div className="px-3 py-1 bg-app rounded-full text-xs font-medium text-accent">
                🔥 7 day streak
              </div>
            </div>

            {/* Theme toggle */}
            <button
              onClick={toggleTheme}
              className="p-2 bg-app rounded-xl border-app border"
            >
              <Sun className="w-5 h-5 text-app hidden dark:block" />
              <Moon className="w-5 h-5 text-app block dark:hidden" />
            </button>

            {/* User info */}
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 gradient-emerald rounded-full flex items-center justify-center text-white text-sm font-bold">
                {userName.charAt(0)}
              </div>
              <span className="text-sm font-medium text-app hidden md:block">{userName}</span>
            </div>
          </div>
        </header>

        {/* Content area */}
        <main className="flex-1 p-4 md:p-6 overflow-y-auto">
          <div className="max-w-4xl mx-auto">
            {renderMainContent()}
          </div>
        </main>

        {/* Bottom nav (mobile) */}
        <nav className="lg:hidden bg-surface border-t border-app p-2 overflow-x-auto">
          <div className="flex gap-2">
            {navItems.slice(0, 8).map((item) => {
              const Icon = item.icon;
              return (
                <button
                  key={item.id}
                  onClick={() => setActiveView(item.id)}
                  className={`flex flex-col items-center gap-1 px-3 py-2 rounded-xl min-w-fit ${
                    activeView === item.id
                      ? 'text-primary'
                      : 'text-muted'
                  }`}
                >
                  <Icon className="w-5 h-5" />
                  <span className="text-xs">{item.label}</span>
                </button>
              );
            })}
          </div>
        </nav>
      </div>
    </div>
  );
}
