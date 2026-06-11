import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Info, User as UserIcon } from 'lucide-react';
import { useAuth } from '../contexts/AuthContext';
import buildInfo from '../buildInfo.json';
import {
  AdminShieldIcon,
  AnalystChartIcon,
  AuditorClipboardIcon,
  CogSixIcon,
  LockShieldIcon,
  LogoutArrowIcon,
  UserCardIcon,
  ViewerEyeIcon,
} from './AppIcons';
import { Avatar, AvatarFallback } from './ui/avatar';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from './ui/dropdown-menu';

const UserMenu: React.FC = () => {
  const [aboutOpen, setAboutOpen] = useState(false);
  const { user, logout, hasPermission } = useAuth();
  const navigate = useNavigate();

  // Build info was previously in a fixed-position footer that occluded
  // table pagination and snackbars.  Moved to the user menu as an
  // "About" dropdown item so the info is still one click away but
  // doesn't compete with task UI.
  const buildTime =
    process.env.REACT_APP_BUILD_TIME || buildInfo.buildTime || new Date().toISOString();
  const frontendVersion =
    process.env.REACT_APP_VERSION || buildInfo.frontendVersion || '1.0.0';
  const backendVersion =
    process.env.REACT_APP_BACKEND_VERSION || buildInfo.backendVersion || 'unknown';
  const gitCommit = process.env.REACT_APP_GIT_COMMIT || buildInfo.gitCommit || 'dev';

  const renderRoleIcon = (role: string) => {
    const cls = 'size-3.5';
    switch (role) {
      case 'admin':
        return <AdminShieldIcon className={cls} />;
      case 'analyst':
        return <AnalystChartIcon className={cls} />;
      case 'auditor':
        return <AuditorClipboardIcon className={cls} />;
      case 'viewer':
        return <ViewerEyeIcon className={cls} />;
      default:
        return <UserIcon className={cls} aria-hidden />;
    }
  };

  const roleBadgeVariant = (role: string): 'destructive' | 'warning' | 'info' | 'success' | 'outline' => {
    switch (role) {
      case 'admin':
        return 'destructive';
      case 'analyst':
        return 'warning';
      case 'auditor':
        return 'info';
      case 'viewer':
        return 'success';
      default:
        return 'outline';
    }
  };

  if (!user) return null;

  const initial = user.username.charAt(0).toUpperCase();
  const displayName = user.full_name || user.username;

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            className="rounded-full focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
            aria-label="Open user menu"
          >
            <Avatar className="size-8 bg-primary text-primary-foreground">
              <AvatarFallback className="bg-primary text-primary-foreground">
                {initial}
              </AvatarFallback>
            </Avatar>
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-72">
          <div className="px-md py-sm">
            <div className="mb-xs flex items-center gap-sm">
              <Avatar className="size-10 bg-primary text-primary-foreground">
                <AvatarFallback className="bg-primary text-metadata text-primary-foreground">
                  {initial}
                </AvatarFallback>
              </Avatar>
              <div className="min-w-0">
                <div className="truncate text-body font-medium">{displayName}</div>
                <div className="text-metadata text-muted-foreground">{user.role}</div>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-xs">
              <Badge variant={roleBadgeVariant(user.role)} className="gap-xxs">
                {renderRoleIcon(user.role)}
                {user.role.toUpperCase()}
              </Badge>
              <Badge variant="success" className="gap-xxs">
                <LockShieldIcon className="size-3.5" />
                Authenticated
              </Badge>
            </div>
          </div>

          <DropdownMenuSeparator />

          <DropdownMenuItem onSelect={() => navigate('/profile')}>
            <UserCardIcon className="size-4" />
            <div>
              <div className="text-metadata">Profile</div>
              <div className="text-caption text-muted-foreground">View and edit profile</div>
            </div>
          </DropdownMenuItem>

          {hasPermission('admin') && (
            <DropdownMenuItem onSelect={() => navigate('/system-settings')}>
              <CogSixIcon className="size-4" />
              <div>
                <div className="text-metadata">System Settings</div>
                <div className="text-caption text-muted-foreground">
                  Manage users and security
                </div>
              </div>
            </DropdownMenuItem>
          )}

          <DropdownMenuSeparator />

          <DropdownMenuItem onSelect={() => setAboutOpen(true)}>
            <Info className="size-4" aria-hidden />
            <div>
              <div className="text-metadata">About BlueStick</div>
              <div className="text-caption text-muted-foreground">
                v{frontendVersion} · API {backendVersion}
              </div>
            </div>
          </DropdownMenuItem>

          <DropdownMenuSeparator />

          <DropdownMenuItem onSelect={logout}>
            <LogoutArrowIcon className="size-4 text-destructive" />
            <div>
              <div className="text-metadata text-destructive">Sign Out</div>
              <div className="text-caption text-muted-foreground">End your session</div>
            </div>
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <Dialog open={aboutOpen} onOpenChange={setAboutOpen}>
        <DialogContent size="sm">
          <DialogHeader>
            <DialogTitle>About BlueStick</DialogTitle>
            <DialogDescription>
              Build versions and identity for this deployment.  Useful
              when filing bug reports or correlating logs across
              services.
            </DialogDescription>
          </DialogHeader>
          <dl className="space-y-sm">
            <div>
              <dt className="text-caption text-muted-foreground">Frontend version</dt>
              <dd className="font-mono">v{frontendVersion}</dd>
            </div>
            <div>
              <dt className="text-caption text-muted-foreground">Backend / API version</dt>
              <dd className="font-mono">{backendVersion}</dd>
            </div>
            <div>
              <dt className="text-caption text-muted-foreground">Build timestamp</dt>
              <dd>{new Date(buildTime).toLocaleString()}</dd>
            </div>
            <div>
              <dt className="text-caption text-muted-foreground">Git commit</dt>
              <dd className="font-mono">
                {/* Audit FRX·L3: when REACT_APP_REPO_URL is configured
                    at build time, the commit hash deep-links to the
                    upstream repo browser.  Falls back to plain text
                    when unset so on-prem builds without a repo URL
                    still render cleanly. */}
                {process.env.REACT_APP_REPO_URL && gitCommit && gitCommit !== 'dev' ? (
                  <a
                    href={`${process.env.REACT_APP_REPO_URL}/commit/${gitCommit}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-primary underline-offset-4 hover:underline"
                  >
                    {gitCommit.substring(0, 12)}
                  </a>
                ) : (
                  gitCommit.substring(0, 12)
                )}
              </dd>
            </div>
            <div>
              <dt className="text-caption text-muted-foreground">Author</dt>
              <dd>Kevin M - Remember me fondly!</dd>
            </div>
          </dl>
          <DialogFooter>
            <Button variant="outline" onClick={() => setAboutOpen(false)}>
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
};

export default UserMenu;
