import { useState, useEffect } from 'react';
import LoginPage from './views/LoginPage';
import OnboardingQuiz from './views/OnboardingQuiz';
import Dashboard from './views/Dashboard';

type AppState = 'login' | 'onboarding' | 'app';

function App() {
  const [appState, setAppState] = useState<AppState>('login');

  useEffect(() => {
    // Check localStorage on mount
    const token = localStorage.getItem('vj_token');
    const userName = localStorage.getItem('vj_user_name');
    const onboarded = localStorage.getItem('vj_onboarded');

    if (token && userName) {
      if (onboarded === 'true') {
        setAppState('app');
      } else {
        setAppState('onboarding');
      }
    } else {
      setAppState('login');
    }
  }, []);

  const handleLogin = (userName?: string) => {
    localStorage.setItem('vj_token', 'fake-login-token');
    localStorage.setItem('vj_user_name', userName || 'Student');
    setAppState('onboarding');
  };

  const handleOnboardingComplete = () => {
    localStorage.setItem('vj_onboarded', 'true');
    setAppState('app');
  };

  const handleLogout = () => {
    // Remove all vj_* keys
    Object.keys(localStorage).forEach((key) => {
      if (key.startsWith('vj_')) {
        localStorage.removeItem(key);
      }
    });
    setAppState('login');
  };

  return (
    <div className="bg-app min-h-screen text-app">
      {appState === 'login' && <LoginPage onLogin={handleLogin} />}
      {appState === 'onboarding' && <OnboardingQuiz onComplete={handleOnboardingComplete} />}
      {appState === 'app' && <Dashboard onLogout={handleLogout} />}
    </div>
  );
}

export default App;
