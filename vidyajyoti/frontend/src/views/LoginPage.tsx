import { BookOpen, Trophy, Target, Users } from 'lucide-react';

interface LoginPageProps {
  onLogin: (userName?: string) => void;
}

export default function LoginPage({ onLogin }: LoginPageProps) {
  return (
    <div className="min-h-screen flex">
      {/* Left side - Branding (hidden on mobile) */}
      <div className="hidden lg:flex lg:w-1/2 bg-surface p-12 flex-col justify-between">
        <div>
          <div className="flex items-center gap-3 mb-8">
            <div className="w-12 h-12 gradient-emerald rounded-xl flex items-center justify-center">
              <BookOpen className="w-7 h-7 text-white" />
            </div>
            <h1 className="text-2xl font-bold text-gradient-emerald">VidyaJyoti</h1>
          </div>
          
          <h2 className="text-4xl font-bold text-app mb-6">
            Your Personal Exam Preparation Companion
          </h2>
          <p className="text-muted text-lg mb-8">
            Smart study plans, gamified learning, and personalized guidance for JEE, NEET, CBSE, and more.
          </p>

          <div className="grid grid-cols-2 gap-4">
            <div className="bg-app p-4 rounded-xl border-app border">
              <Target className="w-8 h-8 text-primary mb-3" />
              <h3 className="font-semibold text-app mb-1">Target Exams</h3>
              <p className="text-muted text-sm">JEE Main, NEET, CBSE, SSC</p>
            </div>
            <div className="bg-app p-4 rounded-xl border-app border">
              <Trophy className="w-8 h-8 text-accent mb-3" />
              <h3 className="font-semibold text-app mb-1">Gamified Learning</h3>
              <p className="text-muted text-sm">Earn points, compete with peers</p>
            </div>
            <div className="bg-app p-4 rounded-xl border-app border">
              <BookOpen className="w-8 h-8 text-primary mb-3" />
              <h3 className="font-semibold text-app mb-1">Smart Plans</h3>
              <p className="text-muted text-sm">AI-powered study schedules</p>
            </div>
            <div className="bg-app p-4 rounded-xl border-app border">
              <Users className="w-8 h-8 text-accent mb-3" />
              <h3 className="font-semibold text-app mb-1">Community</h3>
              <p className="text-muted text-sm">Learn together, grow together</p>
            </div>
          </div>
        </div>

        <p className="text-muted text-sm">© 2024 VidyaJyoti. All rights reserved.</p>
      </div>

      {/* Right side - Login card */}
      <div className="w-full lg:w-1/2 flex items-center justify-center p-8 bg-app">
        <div className="w-full max-w-md">
          <div className="lg:hidden flex items-center gap-3 mb-8">
            <div className="w-10 h-10 gradient-emerald rounded-xl flex items-center justify-center">
              <BookOpen className="w-6 h-6 text-white" />
            </div>
            <h1 className="text-xl font-bold text-gradient-emerald">VidyaJyoti</h1>
          </div>

          <div className="bg-surface p-8 rounded-2xl border-app border shadow-xl">
            <div className="text-center mb-8">
              <h2 className="text-2xl font-bold text-app mb-2">Welcome Back!</h2>
              <p className="text-muted">Sign in to continue your learning journey</p>
            </div>

            <button
              onClick={() => onLogin('Student')}
              className="w-full gradient-emerald text-white font-semibold py-3 px-6 rounded-xl hover:opacity-90 transition-opacity"
            >
              Login as Student
            </button>

            <p className="text-muted text-sm text-center mt-6">
              By logging in, you agree to our Terms of Service and Privacy Policy.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
