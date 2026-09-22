import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Sun, Moon, Home, School, Utensils, BookOpen, Clock, CheckCircle, ArrowRight, Plus } from 'lucide-react';

interface OnboardingQuizProps {
  onComplete: () => void;
}

interface DayPlanSlot {
  start: number;
  end: number;
  type: 'study' | 'meal' | 'school' | 'routine' | 'break' | 'sleep' | 'travel';
  label: string;
}

// Time conversion helpers
function t2m(timeString: string): number {
  const [hours, minutes] = timeString.split(':').map(Number);
  return hours * 60 + minutes;
}

function m2t(minutes: number): string {
  const h = Math.floor(minutes / 60) % 24;
  const m = minutes % 60;
  return `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}`;
}

export default function OnboardingQuiz({ onComplete }: OnboardingQuizProps) {
  const [step, setStep] = useState(0);
  
  // Form state
  const [educationType, setEducationType] = useState<'school' | 'home_school' | 'coaching'>('school');
  const [coachingSubtype, setCoachingSubtype] = useState<'dummy' | 'residential' | ''>('');
  const [grade, setGrade] = useState('11');
  const [targetExams, setTargetExams] = useState<string[]>([]);
  const [morningTasks, setMorningTasks] = useState<string[]>([]);
  const [customMorningTask, setCustomMorningTask] = useState('');
  const [wakeTime, setWakeTime] = useState('06:00');
  const [sleepTime, setSleepTime] = useState('23:00');
  const [leaveHome, setLeaveHome] = useState('07:30');
  const [backHome, setBackHome] = useState('15:00');
  const [commuteMinutes, setCommuteMinutes] = useState(30);
  const [coachingStart, setCoachingStart] = useState('16:00');
  const [coachingEnd, setCoachingEnd] = useState('20:00');
  const [breakfastTime, setBreakfastTime] = useState('08:00');
  const [lunchTime, setLunchTime] = useState('13:00');
  const [dinnerTime, setDinnerTime] = useState('20:30');
  const [mealsPerDay, setMealsPerDay] = useState(3);
  const [peakProductivity, setPeakProductivity] = useState<'morning' | 'afternoon' | 'evening' | 'night'>('morning');
  const [studySessionMinutes, setStudySessionMinutes] = useState(45);
  const [breakMinutes, setBreakMinutes] = useState(10);
  
  const [dayPlan, setDayPlan] = useState<DayPlanSlot[]>([]);

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

  // Generate day plan based on rules
  const generateDayPlan = () => {
    const wakeMins = t2m(wakeTime);
    const sleepMins = t2m(sleepTime);
    
    const fixedBlocks: { start: number; end: number; type: DayPlanSlot['type']; label: string }[] = [];

    // Morning routine tasks
    let routineStart = wakeMins;
    morningTasks.forEach((task) => {
      fixedBlocks.push({
        start: routineStart,
        end: routineStart + 15,
        type: 'routine',
        label: task,
      });
      routineStart += 15;
    });

    // Meals
    const breakfastMins = t2m(breakfastTime);
    if (breakfastMins >= wakeMins) {
      fixedBlocks.push({ start: breakfastMins, end: breakfastMins + 20, type: 'meal', label: 'Breakfast' });
    }
    
    const lunchMins = t2m(lunchTime);
    fixedBlocks.push({ start: lunchMins, end: lunchMins + 30, type: 'meal', label: 'Lunch' });
    
    const dinnerMins = t2m(dinnerTime);
    fixedBlocks.push({ start: dinnerMins, end: dinnerMins + 30, type: 'meal', label: 'Dinner' });

    // School/Coaching
    if (educationType === 'school' || educationType === 'coaching') {
      const leaveMins = t2m(leaveHome);
      const backMins = t2m(backHome);
      
      // Travel to school
      fixedBlocks.push({
        start: leaveMins - commuteMinutes,
        end: leaveMins,
        type: 'travel',
        label: 'Commute to school',
      });
      
      // School
      fixedBlocks.push({
        start: leaveMins,
        end: backMins,
        type: 'school',
        label: educationType === 'school' ? 'School' : 'Coaching',
      });
      
      // Travel home
      fixedBlocks.push({
        start: backMins,
        end: backMins + commuteMinutes,
        type: 'travel',
        label: 'Commute home',
      });
    }

    // Residential coaching
    if (coachingSubtype === 'residential') {
      const coachStartMins = t2m(coachingStart);
      const coachEndMins = t2m(coachingEnd);
      fixedBlocks.push({
        start: coachStartMins,
        end: coachEndMins,
        type: 'school',
        label: 'Residential Coaching',
      });
    }

    // Sleep before wake (00:00 to wake time)
    if (wakeMins > 0) {
      fixedBlocks.push({ start: 0, end: wakeMins, type: 'sleep', label: 'Sleep' });
    }

    // Sort by start time
    fixedBlocks.sort((a, b) => a.start - b.start);

    // Fill study sessions between fixed blocks
    const finalSlots: DayPlanSlot[] = [];
    let cursor = wakeMins;

    for (const block of fixedBlocks) {
      if (block.start > cursor) {
        // Free time gap - fill with study sessions
        fillStudySessions(cursor, block.start, finalSlots);
      }
      finalSlots.push(block);
      cursor = block.end;
    }

    // After all fixed blocks until sleep time
    if (cursor < sleepMins) {
      fillStudySessions(cursor, sleepMins, finalSlots);
    }

    // Final sleep block
    finalSlots.push({ start: sleepMins, end: 1439, type: 'sleep', label: 'Sleep' });

    setDayPlan(finalSlots);
  };

  const fillStudySessions = (start: number, end: number, slots: DayPlanSlot[]) => {
    const gap = end - start;
    
    if (gap < 20) {
      if (gap >= 5) {
        slots.push({ start, end, type: 'break', label: 'Short break/free time' });
      }
      return;
    }

    let current = start;
    let sessionNum = 1;

    while (current + studySessionMinutes <= end) {
      // Add study session
      slots.push({
        start: current,
        end: current + studySessionMinutes,
        type: 'study',
        label: `Study session ${sessionNum}`,
      });
      current += studySessionMinutes;
      sessionNum++;

      // Check if we can add a break and another session
      if (current + breakMinutes + studySessionMinutes <= end) {
        slots.push({
          start: current,
          end: current + breakMinutes,
          type: 'break',
          label: 'Break',
        });
        current += breakMinutes;
      }
    }

    // Leftover time
    const leftover = end - current;
    if (leftover >= 5) {
      slots.push({
        start: current,
        end,
        type: 'break',
        label: 'Free time',
      });
    }
  };

  const handleNext = () => {
    if (step === 5) {
      generateDayPlan();
    }
    if (step < 6) {
      setStep(step + 1);
    }
  };

  const handlePrev = () => {
    if (step > 0) {
      setStep(step - 1);
    }
  };

  const toggleExam = (exam: string) => {
    setTargetExams(prev =>
      prev.includes(exam) ? prev.filter(e => e !== exam) : [...prev, exam]
    );
  };

  const toggleMorningTask = (task: string) => {
    setMorningTasks(prev =>
      prev.includes(task) ? prev.filter(t => t !== task) : [...prev, task]
    );
  };

  const addCustomMorningTask = () => {
    if (customMorningTask.trim()) {
      setMorningTasks(prev => [...prev, customMorningTask.trim()]);
      setCustomMorningTask('');
    }
  };

  const getSlotIcon = (type: DayPlanSlot['type']) => {
    switch (type) {
      case 'study': return <BookOpen className="w-5 h-5 text-primary" />;
      case 'meal': return <Utensils className="w-5 h-5 text-accent" />;
      case 'school': return <School className="w-5 h-5 text-primary" />;
      case 'routine': return <Sun className="w-5 h-5 text-accent" />;
      case 'break': return <Moon className="w-5 h-5 text-muted" />;
      case 'sleep': return <Moon className="w-5 h-5 text-muted" />;
      case 'travel': return <Clock className="w-5 h-5 text-accent" />;
      default: return <CheckCircle className="w-5 h-5" />;
    }
  };

  const renderStep = () => {
    switch (step) {
      case 0:
        return (
          <div className="space-y-6">
            <h3 className="text-xl font-bold text-app">Education Type</h3>
            <div className="space-y-4">
              {['school', 'home_school', 'coaching'].map((type) => (
                <button
                  key={type}
                  onClick={() => setEducationType(type as any)}
                  className={`w-full p-4 rounded-xl border text-left transition-colors ${
                    educationType === type
                      ? 'border-primary bg-surface-hover'
                      : 'border-app bg-surface'
                  }`}
                >
                  <span className="capitalize text-app">{type.replace('_', ' ')}</span>
                </button>
              ))}
            </div>
            {educationType === 'coaching' && (
              <div className="space-y-4 mt-4">
                <h4 className="text-lg font-semibold text-app">Coaching Type</h4>
                {['dummy', 'residential'].map((subtype) => (
                  <button
                    key={subtype}
                    onClick={() => setCoachingSubtype(subtype as any)}
                    className={`w-full p-4 rounded-xl border text-left transition-colors ${
                      coachingSubtype === subtype
                        ? 'border-primary bg-surface-hover'
                        : 'border-app bg-surface'
                    }`}
                  >
                    <span className="capitalize text-app">{subtype}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        );

      case 1:
        return (
          <div className="space-y-6">
            <h3 className="text-xl font-bold text-app">Grade & Target Exams</h3>
            <div>
              <label className="text-muted text-sm mb-2 block">Grade</label>
              <select value={grade} onChange={(e) => setGrade(e.target.value)}>
                {[9, 10, 11, 12].map(g => (
                  <option key={g} value={g}>Class {g}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="text-muted text-sm mb-2 block">Target Exams</label>
              <div className="flex flex-wrap gap-2">
                {['JEE Main', 'JEE Advanced', 'NEET', 'CBSE Board', 'SSC', 'Other'].map((exam) => (
                  <button
                    key={exam}
                    onClick={() => toggleExam(exam)}
                    className={`px-4 py-2 rounded-full text-sm transition-colors ${
                      targetExams.includes(exam)
                        ? 'gradient-emerald text-white'
                        : 'bg-surface text-muted border-app border'
                    }`}
                  >
                    {exam}
                  </button>
                ))}
              </div>
            </div>
          </div>
        );

      case 2:
        return (
          <div className="space-y-6">
            <h3 className="text-xl font-bold text-app">Morning Routine</h3>
            <p className="text-muted text-sm">Select your morning tasks (each takes ~15 min)</p>
            <div className="flex flex-wrap gap-2">
              {['Wake up & freshen up', 'Exercise', 'Meditation', 'Breakfast', 'Review notes'].map((task) => (
                <button
                  key={task}
                  onClick={() => toggleMorningTask(task)}
                  className={`px-4 py-2 rounded-full text-sm transition-colors ${
                    morningTasks.includes(task)
                      ? 'gradient-emerald text-white'
                      : 'bg-surface text-muted border-app border'
                  }`}
                >
                  {task}
                </button>
              ))}
            </div>
            <div className="flex gap-2">
              <input
                type="text"
                value={customMorningTask}
                onChange={(e) => setCustomMorningTask(e.target.value)}
                placeholder="Add custom task..."
                className="flex-1"
              />
              <button
                onClick={addCustomMorningTask}
                className="p-2 gradient-emerald rounded-xl text-white"
              >
                <Plus className="w-5 h-5" />
              </button>
            </div>
          </div>
        );

      case 3:
        return (
          <div className="space-y-6">
            <h3 className="text-xl font-bold text-app">Daily Schedule</h3>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-muted text-sm mb-2 block">Wake Time</label>
                <input type="time" value={wakeTime} onChange={(e) => setWakeTime(e.target.value)} />
              </div>
              <div>
                <label className="text-muted text-sm mb-2 block">Sleep Time</label>
                <input type="time" value={sleepTime} onChange={(e) => setSleepTime(e.target.value)} />
              </div>
              {(educationType === 'school' || educationType === 'coaching') && (
                <>
                  <div>
                    <label className="text-muted text-sm mb-2 block">Leave Home</label>
                    <input type="time" value={leaveHome} onChange={(e) => setLeaveHome(e.target.value)} />
                  </div>
                  <div>
                    <label className="text-muted text-sm mb-2 block">Back Home</label>
                    <input type="time" value={backHome} onChange={(e) => setBackHome(e.target.value)} />
                  </div>
                  <div>
                    <label className="text-muted text-sm mb-2 block">Commute (min)</label>
                    <input type="number" value={commuteMinutes} onChange={(e) => setCommuteMinutes(Number(e.target.value))} />
                  </div>
                </>
              )}
              {coachingSubtype === 'residential' && (
                <>
                  <div>
                    <label className="text-muted text-sm mb-2 block">Coaching Start</label>
                    <input type="time" value={coachingStart} onChange={(e) => setCoachingStart(e.target.value)} />
                  </div>
                  <div>
                    <label className="text-muted text-sm mb-2 block">Coaching End</label>
                    <input type="time" value={coachingEnd} onChange={(e) => setCoachingEnd(e.target.value)} />
                  </div>
                </>
              )}
            </div>
          </div>
        );

      case 4:
        return (
          <div className="space-y-6">
            <h3 className="text-xl font-bold text-app">Meals & Study Preferences</h3>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-muted text-sm mb-2 block">Breakfast Time</label>
                <input type="time" value={breakfastTime} onChange={(e) => setBreakfastTime(e.target.value)} />
              </div>
              <div>
                <label className="text-muted text-sm mb-2 block">Lunch Time</label>
                <input type="time" value={lunchTime} onChange={(e) => setLunchTime(e.target.value)} />
              </div>
              <div>
                <label className="text-muted text-sm mb-2 block">Dinner Time</label>
                <input type="time" value={dinnerTime} onChange={(e) => setDinnerTime(e.target.value)} />
              </div>
              <div>
                <label className="text-muted text-sm mb-2 block">Meals/Day</label>
                <input type="number" value={mealsPerDay} onChange={(e) => setMealsPerDay(Number(e.target.value))} />
              </div>
            </div>
            <div className="mt-4">
              <label className="text-muted text-sm mb-2 block">Peak Productivity</label>
              <select value={peakProductivity} onChange={(e) => setPeakProductivity(e.target.value as any)}>
                <option value="morning">Morning</option>
                <option value="afternoon">Afternoon</option>
                <option value="evening">Evening</option>
                <option value="night">Night</option>
              </select>
            </div>
            <div className="grid grid-cols-2 gap-4 mt-4">
              <div>
                <label className="text-muted text-sm mb-2 block">Study Session (min)</label>
                <input type="number" value={studySessionMinutes} onChange={(e) => setStudySessionMinutes(Number(e.target.value))} />
              </div>
              <div>
                <label className="text-muted text-sm mb-2 block">Break Length (min)</label>
                <input type="number" value={breakMinutes} onChange={(e) => setBreakMinutes(Number(e.target.value))} />
              </div>
            </div>
          </div>
        );

      case 5:
        return (
          <div className="space-y-6">
            <h3 className="text-xl font-bold text-app">Your Generated Day Plan</h3>
            <p className="text-muted text-sm">Based on your preferences, here's your personalized schedule</p>
            
            <div className="space-y-2 max-h-96 overflow-y-auto">
              {dayPlan.map((slot, idx) => (
                <div key={idx} className="flex items-center gap-3 p-3 bg-surface rounded-xl border-app border">
                  {getSlotIcon(slot.type)}
                  <div className="flex-1">
                    <p className="text-app font-medium">{slot.label}</p>
                    <p className="text-muted text-sm">{m2t(slot.start)} – {m2t(slot.end)}</p>
                  </div>
                </div>
              ))}
            </div>

            <div className="grid grid-cols-3 gap-4 pt-4">
              <div className="bg-surface p-3 rounded-xl text-center">
                <p className="text-muted text-xs">Study Sessions</p>
                <p className="text-2xl font-bold text-primary">{dayPlan.filter(s => s.type === 'study').length}</p>
              </div>
              <div className="bg-surface p-3 rounded-xl text-center">
                <p className="text-muted text-xs">Total Study Time</p>
                <p className="text-2xl font-bold text-accent">
                  {Math.round(dayPlan.filter(s => s.type === 'study').reduce((acc, s) => acc + (s.end - s.start), 0) / 60)}h
                </p>
              </div>
              <div className="bg-surface p-3 rounded-xl text-center">
                <p className="text-muted text-xs">Break Time</p>
                <p className="text-2xl font-bold text-primary">
                  {Math.round(dayPlan.filter(s => s.type === 'break').reduce((acc, s) => acc + (s.end - s.start), 0) / 60)}h
                </p>
              </div>
            </div>
          </div>
        );

      case 6:
        return (
          <div className="space-y-6 text-center">
            <h3 className="text-2xl font-bold text-app">You're All Set!</h3>
            <p className="text-muted">Your personalized study plan is ready. Let's begin your learning journey!</p>
            <div className="bg-surface p-6 rounded-xl border-app border">
              <CheckCircle className="w-16 h-16 text-primary mx-auto mb-4" />
              <p className="text-app font-semibold">Onboarding Complete</p>
            </div>
          </div>
        );

      default:
        return null;
    }
  };

  return (
    <div className="min-h-screen bg-app flex flex-col">
      {/* Header */}
      <div className="p-4 flex justify-between items-center border-b border-app">
        <div className="flex items-center gap-2">
          <span className="text-muted text-sm">Step {step + 1} of 7</span>
          <div className="w-32 h-2 bg-surface rounded-full overflow-hidden">
            <div
              className="h-full gradient-emerald transition-all"
              style={{ width: `${((step + 1) / 7) * 100}%` }}
            />
          </div>
        </div>
        <button
          onClick={toggleTheme}
          className="p-2 bg-surface rounded-xl border-app border"
        >
          <Sun className="w-5 h-5 text-app hidden dark:block" />
          <Moon className="w-5 h-5 text-app block dark:hidden" />
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 flex items-center justify-center p-4">
        <div className="w-full max-w-lg">
          <AnimatePresence mode="wait">
            <motion.div
              key={step}
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -20 }}
              transition={{ duration: 0.2 }}
              className="bg-surface p-6 rounded-2xl border-app border shadow-xl"
            >
              {renderStep()}

              {/* Navigation */}
              <div className="flex justify-between mt-8">
                <button
                  onClick={handlePrev}
                  disabled={step === 0}
                  className={`px-6 py-2 rounded-xl font-medium transition-colors ${
                    step === 0
                      ? 'text-muted cursor-not-allowed'
                      : 'text-app bg-surface-hover hover:bg-surface'
                  }`}
                >
                  Back
                </button>
                {step < 6 ? (
                  <button
                    onClick={handleNext}
                    className="px-6 py-2 gradient-emerald text-white rounded-xl font-medium flex items-center gap-2"
                  >
                    Next <ArrowRight className="w-4 h-4" />
                  </button>
                ) : (
                  <button
                    onClick={onComplete}
                    className="px-6 py-2 gradient-emerald text-white rounded-xl font-medium"
                  >
                    Start Learning
                  </button>
                )}
              </div>
            </motion.div>
          </AnimatePresence>
        </div>
      </div>
    </div>
  );
}
