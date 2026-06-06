/**
 * Comprehensive Logging Utility
 * Provides debugging and audit trail functionality
 */

export enum LogLevel {
  DEBUG = 0,
  INFO = 1,
  WARN = 2,
  ERROR = 3,
  AUDIT = 4
}

export interface LogEntry {
  timestamp: string;
  level: LogLevel;
  category: string;
  message: string;
  details?: Record<string, unknown>;
  userId?: string;
  sessionId?: string;
  stackTrace?: string;
}

class Logger {
  private logs: LogEntry[] = [];
  private maxLogs = 1000;
  private currentLogLevel = LogLevel.DEBUG;

  private formatTimestamp(): string {
    return new Date().toISOString();
  }

  private getStackTrace(): string {
    try {
      throw new Error();
    } catch (e) {
      return (e as Error).stack || 'No stack trace available';
    }
  }

  private createLogEntry(
    level: LogLevel,
    category: string,
    message: string,
    details?: Record<string, unknown>,
    includeStack = false
  ): LogEntry {
    const entry: LogEntry = {
      timestamp: this.formatTimestamp(),
      level,
      category,
      message,
      details,
      userId: this.getCurrentUserId(),
      sessionId: this.getSessionId()
    };

    if (includeStack || level === LogLevel.ERROR) {
      entry.stackTrace = this.getStackTrace();
    }

    return entry;
  }

  private getCurrentUserId(): string | undefined {
    try {
      const userStr = localStorage.getItem('auth_user');
      if (userStr) {
        const user = JSON.parse(userStr);
        return user.id?.toString();
      }
    } catch (e) {
      // Ignore parsing errors
    }
    return undefined;
  }

  private getSessionId(): string {
    let sessionId = sessionStorage.getItem('debug_session_id');
    if (!sessionId) {
      sessionId = `sess_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
      sessionStorage.setItem('debug_session_id', sessionId);
    }
    return sessionId;
  }

  private addLog(entry: LogEntry): void {
    this.logs.push(entry);

    // Keep only the most recent logs
    if (this.logs.length > this.maxLogs) {
      this.logs = this.logs.slice(-this.maxLogs);
    }

    // Console output for immediate debugging
    this.outputToConsole(entry);
  }

  private outputToConsole(entry: LogEntry): void {
    const prefix = `[${entry.timestamp}] [${LogLevel[entry.level]}] [${entry.category}]`;
    const message = `${prefix} ${entry.message}`;

    switch (entry.level) {
      case LogLevel.DEBUG:
        console.debug(message, entry.details);
        break;
      case LogLevel.INFO:
        console.info(message, entry.details);
        break;
      case LogLevel.WARN:
        console.warn(message, entry.details);
        break;
      case LogLevel.ERROR:
        console.error(message, entry.details, entry.stackTrace);
        break;
      case LogLevel.AUDIT:
        console.log(`🔍 AUDIT: ${message}`, entry.details);
        break;
    }
  }

  // Public logging methods
  debug(category: string, message: string, details?: Record<string, unknown>): void {
    if (this.currentLogLevel <= LogLevel.DEBUG) {
      this.addLog(this.createLogEntry(LogLevel.DEBUG, category, message, details));
    }
  }

  info(category: string, message: string, details?: Record<string, unknown>): void {
    if (this.currentLogLevel <= LogLevel.INFO) {
      this.addLog(this.createLogEntry(LogLevel.INFO, category, message, details));
    }
  }

  warn(category: string, message: string, details?: Record<string, unknown>): void {
    if (this.currentLogLevel <= LogLevel.WARN) {
      this.addLog(this.createLogEntry(LogLevel.WARN, category, message, details));
    }
  }

  error(category: string, message: string, details?: Record<string, unknown>): void {
    this.addLog(this.createLogEntry(LogLevel.ERROR, category, message, details, true));
  }

  audit(category: string, action: string, details?: Record<string, unknown>): void {
    this.addLog(this.createLogEntry(LogLevel.AUDIT, category, `AUDIT: ${action}`, details));
  }

  // Authentication-specific logging
  authDebug(message: string, details?: Record<string, unknown>): void {
    this.debug('AUTH', message, details);
  }

  authInfo(message: string, details?: Record<string, unknown>): void {
    this.info('AUTH', message, details);
  }

  authError(message: string, details?: Record<string, unknown>): void {
    this.error('AUTH', message, details);
  }

  authAudit(action: string, details?: Record<string, unknown>): void {
    this.audit('AUTH', action, details);
  }

  // Utility methods
  setLogLevel(level: LogLevel): void {
    this.currentLogLevel = level;
    this.info('LOGGER', `Log level set to ${LogLevel[level]}`);
  }

  getLogs(category?: string, level?: LogLevel): LogEntry[] {
    let filteredLogs = [...this.logs];

    if (category) {
      filteredLogs = filteredLogs.filter(log => log.category === category);
    }

    if (level !== undefined) {
      filteredLogs = filteredLogs.filter(log => log.level >= level);
    }

    return filteredLogs;
  }

  getAuthLogs(): LogEntry[] {
    return this.getLogs('AUTH');
  }

  clearLogs(): void {
    this.logs = [];
    this.info('LOGGER', 'Logs cleared');
  }

  exportLogs(): string {
    return JSON.stringify(this.logs, null, 2);
  }

  // Performance tracking
  startTimer(label: string): () => void {
    const startTime = performance.now();
    this.debug('PERF', `Timer started: ${label}`);

    return () => {
      const endTime = performance.now();
      const duration = endTime - startTime;
      this.info('PERF', `Timer completed: ${label}`, { duration: `${duration.toFixed(2)}ms` });
    };
  }
}

// Create singleton instance
export const logger = new Logger();

// Global error handler.
//
// `ResizeObserver loop completed with undelivered notifications.` (and
// the older `ResizeObserver loop limit exceeded`) are benign browser
// warnings that fire when an observer callback triggers a layout the
// observer then sees on the next tick. The browser immediately reruns
// the next frame and nothing is actually broken — but our handler used
// to surface them as "Unhandled error" with empty filename/line, which
// flooded the console on hub-landing renders (Layout's chrome-height
// observer is a common trigger). Drop them on the floor so the
// console signal stays meaningful.
const RESIZE_OBSERVER_WARNINGS = /^ResizeObserver loop /;

window.addEventListener('error', (event) => {
  if (event.message && RESIZE_OBSERVER_WARNINGS.test(event.message)) {
    event.stopImmediatePropagation();
    return;
  }
  logger.error('GLOBAL', 'Unhandled error', {
    message: event.message,
    filename: event.filename,
    lineno: event.lineno,
    colno: event.colno,
    error: event.error
  });
});

window.addEventListener('unhandledrejection', (event) => {
  logger.error('GLOBAL', 'Unhandled promise rejection', {
    reason: event.reason
  });
});

// Export utility functions
export const createAuthLogger = () => ({
  debug: (message: string, details?: Record<string, unknown>) => logger.authDebug(message, details),
  info: (message: string, details?: Record<string, unknown>) => logger.authInfo(message, details),
  warn: (message: string, details?: Record<string, unknown>) => logger.warn('AUTH', message, details),
  error: (message: string, details?: Record<string, unknown>) => logger.authError(message, details),
  audit: (action: string, details?: Record<string, unknown>) => logger.authAudit(action, details),
  timer: (label: string) => logger.startTimer(`AUTH: ${label}`)
});

export default logger;