const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

// Read version from package.json
let packageVersion = '1.0.0';
try {
  const packageJson = JSON.parse(fs.readFileSync(path.join(__dirname, 'package.json'), 'utf8'));
  packageVersion = packageJson.version;
} catch (error) {
  console.warn('Could not read package.json version:', error.message);
}

// Generate build information
const buildInfo = {
  BUILD_TIME: new Date().toISOString(),
  VERSION: packageVersion,
  GIT_COMMIT: 'unknown',
  BACKEND_VERSION: 'unknown'
};

// v2.44.1 — env var wins, no questions asked.  Docker builds set this
// via the BACKEND_VERSION build-arg → ENV in frontend/Dockerfile, so
// the file-read path below is only used during local
// `npm run build` from a checked-out repo.  Pre-fix the file read ran
// first and emitted "Could not read backend version: ENOENT" on every
// Docker build (the build context is just frontend/, so
// ../platform_version.json doesn't exist) — that warning looked like a
// deploy failure on fresh hosts even though the env var was already set.
if (process.env.BACKEND_VERSION) {
  buildInfo.BACKEND_VERSION = process.env.BACKEND_VERSION;
} else if (process.env.REACT_APP_BACKEND_VERSION) {
  buildInfo.BACKEND_VERSION = process.env.REACT_APP_BACKEND_VERSION;
} else {
  // Local-dev fallback — only warn if we genuinely couldn't find a
  // version anywhere (this is the "developer ran npm run build outside
  // the repo" case).
  try {
    const platformVersionPath = path.join(__dirname, '..', 'platform_version.json');
    const platformVersions = JSON.parse(fs.readFileSync(platformVersionPath, 'utf8'));
    if (platformVersions.backend) {
      buildInfo.BACKEND_VERSION = platformVersions.backend;
    }
  } catch (error) {
    console.warn(
      'Could not read backend version (set BACKEND_VERSION env or place platform_version.json at repo root):',
      error.message,
    );
  }
}

// v2.44.1 — best-effort git commit hash.  Skip silently when there's no
// .git/ at the repo root (Docker builds copy only frontend/ into context,
// CI tarball deploys don't ship .git either).  Pre-fix bare
// `git rev-parse HEAD` wrote "fatal: not a git repository" to stderr
// before our catch ran, which polluted deploy logs and read like a
// genuine error.
const repoRoot = path.join(__dirname, '..');
if (process.env.GIT_COMMIT) {
  buildInfo.GIT_COMMIT = process.env.GIT_COMMIT;
} else if (process.env.REACT_APP_GIT_COMMIT) {
  buildInfo.GIT_COMMIT = process.env.REACT_APP_GIT_COMMIT;
} else if (fs.existsSync(path.join(repoRoot, '.git'))) {
  try {
    buildInfo.GIT_COMMIT = execSync('git rev-parse HEAD', {
      encoding: 'utf8',
      cwd: repoRoot,
      // Silence git's own stderr — the catch below logs once with
      // context if anything goes wrong.
      stdio: ['ignore', 'pipe', 'ignore'],
    }).trim();
  } catch (error) {
    console.warn('Could not get git commit hash:', error.message);
  }
}
// If !hasGitDir and no env override, GIT_COMMIT stays 'unknown' silently
// — that's the expected state for any container build and shouldn't
// produce stderr noise.

// Create .env file for build
const envContent = Object.entries(buildInfo)
  .map(([key, value]) => `REACT_APP_${key}=${value}`)
  .join('\n');

fs.writeFileSync(path.join(__dirname, '.env.local'), envContent);

// Persist build info for the application bundle
const buildInfoJson = {
  frontendVersion: buildInfo.VERSION,
  backendVersion: buildInfo.BACKEND_VERSION,
  buildTime: buildInfo.BUILD_TIME,
  gitCommit: buildInfo.GIT_COMMIT,
};

fs.writeFileSync(
  path.join(__dirname, 'src', 'buildInfo.json'),
  `${JSON.stringify(buildInfoJson, null, 2)}\n`
);

console.log('Build info generated:');
console.log(envContent);
