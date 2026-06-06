import React, { useEffect } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import { ArrowLeft, Home, Loader2 } from 'lucide-react';
import { useAuth } from '../contexts/AuthContext';
import { logger } from '../utils/logger';
import { Button } from './ui/button';

interface ProtectedRouteProps {
  children: React.ReactNode;
  requiredRole?: string;
}

const ProtectedRoute: React.FC<ProtectedRouteProps> = ({ children, requiredRole }) => {
  const { isAuthenticated, isLoading, hasPermission, user } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();

  useEffect(() => {
    logger.debug('PROTECTED_ROUTE', 'ProtectedRoute render', {
      pathname: location.pathname,
      requiredRole,
      isAuthenticated,
      isLoading,
      hasUser: !!user,
      userId: user?.id,
      username: user?.username,
      userRole: user?.role,
      hasPermission: requiredRole ? hasPermission(requiredRole) : true,
    });
  });

  if (isLoading) {
    logger.debug('PROTECTED_ROUTE', 'Showing loading spinner', {
      pathname: location.pathname,
      requiredRole,
    });
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-sm text-muted-foreground">
        <Loader2 className="size-8 animate-spin" aria-hidden />
        <p className="text-metadata">Verifying authentication…</p>
      </div>
    );
  }

  if (
    isAuthenticated &&
    user?.must_change_password &&
    location.pathname !== '/force-change-password'
  ) {
    return <Navigate to="/force-change-password" replace />;
  }

  if (!isAuthenticated) {
    logger.warn('PROTECTED_ROUTE', 'Redirecting to login - not authenticated', {
      pathname: location.pathname,
      requiredRole,
      isAuthenticated,
      hasUser: !!user,
      userId: user?.id,
      username: user?.username,
      action: 'REDIRECT_TO_LOGIN',
    });
    logger.audit('PROTECTED_ROUTE', 'UNAUTHORIZED_ACCESS_ATTEMPT', {
      pathname: location.pathname,
      requiredRole,
      hasUser: !!user,
      userId: user?.id,
    });
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  if (requiredRole && !hasPermission(requiredRole)) {
    logger.warn('PROTECTED_ROUTE', 'Access denied - insufficient permissions', {
      pathname: location.pathname,
      requiredRole,
      userRole: user?.role,
      userId: user?.id,
      username: user?.username,
      action: 'ACCESS_DENIED',
    });
    logger.audit('PROTECTED_ROUTE', 'INSUFFICIENT_PERMISSIONS', {
      pathname: location.pathname,
      requiredRole,
      userRole: user?.role,
      userId: user?.id,
      username: user?.username,
    });
    return (
      <div className="flex min-h-[50vh] flex-col items-center justify-center gap-sm p-lg text-center">
        <h2 className="text-section-title text-destructive">Access Denied</h2>
        <p className="text-body text-muted-foreground">
          You don't have sufficient permissions to access this resource.
        </p>
        <p className="text-metadata text-muted-foreground">
          Required role: <strong>{requiredRole}</strong>
          {' · '}Your role: <strong>{user?.role}</strong>
        </p>
        <p className="mt-xxs text-metadata text-muted-foreground">
          If you believe you should have access, contact a project admin.
        </p>
        <div className="mt-sm flex flex-wrap justify-center gap-xs">
          <Button onClick={() => navigate('/operations')}>
            <Home className="size-4" aria-hidden />
            Go to Operations
          </Button>
          <Button variant="outline" onClick={() => navigate(-1)}>
            <ArrowLeft className="size-4" aria-hidden />
            Go Back
          </Button>
        </div>
      </div>
    );
  }

  logger.debug('PROTECTED_ROUTE', 'Access granted - rendering children', {
    pathname: location.pathname,
    requiredRole,
    userRole: user?.role,
    userId: user?.id,
    username: user?.username,
  });

  return <>{children}</>;
};

export default ProtectedRoute;
