# GitHub Setup Instructions

To push this repository to GitHub, you need to set up authentication using a Personal Access Token (PAT).

## Step 1: Create a Personal Access Token

1. Go to GitHub.com and log in
2. Click your profile picture → Settings
3. Scroll down to "Developer settings" (left sidebar)
4. Click "Personal access tokens" → "Tokens (classic)"
5. Click "Generate new token" → "Generate new token (classic)"
6. Give it a name like "Alexa_W repo access"
7. Select scopes:
   - ✅ repo (all repo permissions)
   - ✅ workflow (if using GitHub Actions)
8. Click "Generate token"
9. **COPY THE TOKEN NOW** (you won't see it again!)

## Step 2: Push to GitHub

### Option A: Using the token directly (one-time push)
```bash
git push https://TrueAndyT:<YOUR_TOKEN>@github.com/TrueAndyT/Alexa_W.git main
```

### Option B: Store credentials (recommended)
```bash
# Configure git to store credentials
git config --global credential.helper store

# Push (you'll be prompted for username and token)
git push -u origin main
# Username: TrueAndyT
# Password: <paste your token here>
```

### Option C: Use GitHub CLI (recommended for long-term)
```bash
# Install GitHub CLI
sudo apt install gh

# Authenticate
gh auth login

# Push
git push -u origin main
```

## Step 3: Verify

After pushing, your repository should be available at:
https://github.com/TrueAndyT/Alexa_W

## Repository Contents

Your repository includes:
- ✅ Complete Logger Service (port 5001)
- ✅ Complete KWD Service (port 5003) with wake word detection
- ✅ Common modules (base service, config, health checks, GPU monitor)
- ✅ Proto definitions for all services
- ✅ Service management script
- ✅ Configuration files
- ✅ Project specifications and tasks
- ❌ Models (excluded due to size, see models/README.md)

## Next Steps

After pushing to GitHub:
1. Add a description to your repository
2. Add topics: `voice-assistant`, `grpc`, `python`, `wake-word-detection`
3. Consider adding GitHub Actions for CI/CD
4. Add collaborators if working with a team

## Security Notes

- Never commit your Personal Access Token
- Token is in .gitignore but be careful with credentials
- All services bind to localhost only (security by design)
