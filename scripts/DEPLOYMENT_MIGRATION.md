# Deployment Script Migration Guide

## Overview

BlueStick has **consolidated and replaced** all deployment scripts with a single unified script: `./scripts/deploy.sh`

This simplifies deployment by providing an interactive menu with all deployment options in one place.

## Migration Map

### Old Scripts → New Unified Options

| Removed Script | New Command | Description |
|----------------|-------------|-------------|
| `setup-network.sh` | `./scripts/deploy.sh` → Option 2 | Network production deployment |
| `force-clean-rebuild.sh` | `./scripts/deploy.sh` → Option 4 | Nuclear clean rebuild |
| `deploy-fresh.sh` | `./scripts/deploy.sh` → Option 5 | Ultra-aggressive network deploy |
| `deploy-test.sh` | `./scripts/deploy.sh` → Option 3 | Test instance deployment |
| `docker-compose up -d` | `./scripts/deploy.sh` → Option 1 | Local development |

### Legacy Script Status

- **Removed**: Old scripts have been removed to simplify the codebase
- **Unified**: All functionality moved to `./scripts/deploy.sh`
- **Documentation**: Updated to reference the new approach only

## Benefits of Unified Script

1. **Single Entry Point**: One script for all deployment scenarios
2. **Interactive Menu**: Clear options with descriptions
3. **Consistent Interface**: Same prompts and feedback across all deployments
4. **Better Error Handling**: Unified error messages and validation
5. **Enhanced Logging**: Integrated with the updated log collection system

## Quick Start

```bash
# Run the unified deployment script
./scripts/deploy.sh

# Follow the interactive prompts to select your deployment type
```

## Deployment Options Explained

### 1. Local Development
- **Purpose**: Quick setup for local development
- **Ports**: Frontend 3000, Backend 8000
- **Database**: Local container
- **Use Case**: Development and testing

### 2. Network Production
- **Purpose**: Production deployment on network IP
- **Requirements**: `.env` file must exist
- **Ports**: Configured IP:3000 and IP:8000
- **Use Case**: Production server deployment

### 3. Test Instance
- **Purpose**: Parallel testing without affecting production
- **Ports**: Frontend 3001, Backend 8001
- **Database**: Separate test database
- **Use Case**: Testing changes alongside production

### 4. Nuclear Clean
- **Purpose**: Complete Docker reset when cache issues persist
- **Warning**: Removes ALL Docker data
- **Use Case**: Severe Docker cache problems

### 5. Ultra-Fresh Network
- **Purpose**: Aggressive cache-busting network deployment
- **Requirements**: `.env` file
- **Use Case**: Network deployment with persistent cache issues

## Cache Busting Options

Each deployment type offers cache control:
- **Quick Build**: Use existing cache (fastest)
- **Clean Build**: Remove app images only (moderate)
- **Nuclear Build**: Remove all images and cache (slowest, most thorough)

## Authentication & Logging Updates

The deployment system now includes:
- **Comprehensive Auth Logging**: Frontend and backend authentication tracking
- **Enhanced Log Collection**: `./scripts/collect-logs.sh` captures auth logs
- **Debug Tools**: Browser console commands for authentication debugging

### Working Test Credentials
```
Username: testadmin2
Password: admin123
Role: admin
```

## Troubleshooting

If you encounter issues:

1. **Check Authentication**: Use browser console commands in the documentation.
2. **Collect Logs**: Run `./scripts/collect-logs.sh` for comprehensive diagnostics
3. **Try Nuclear Option**: Use deployment option 4 for severe cache issues
4. **Verify Environment**: Ensure `.env` is correctly configured

## Script Cleanup

The old deployment scripts have been removed to simplify maintenance:
- All functionality is now in the unified `./scripts/deploy.sh`
- This prevents confusion and ensures everyone uses the same approach
- The unified script provides better error handling and user experience

## Migration Steps

1. **Try the New Script**: Run `./scripts/deploy.sh` to familiarize yourself
2. **Update Workflows**: Replace old script calls with the new unified script
3. **Update Documentation**: Reference the new deployment approach in your docs
4. **Train Team**: Ensure team members know about the new script

## Questions?

- Review the main documentation.
- Check the comprehensive logging guide
- Run `./scripts/collect-logs.sh` for troubleshooting data