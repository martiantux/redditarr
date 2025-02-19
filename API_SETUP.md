# Reddit API Setup Guide

This guide walks you through getting the necessary Reddit API credentials for Redditarr.

## Quick Steps

1. Go to https://www.reddit.com/prefs/apps
2. Click "create another app..."
3. Fill in:
   - name: redditarr (or any name you prefer)
   - type: script
   - description: Personal media archiver
   - redirect uri: http://localhost:8481
4. Click "create app"
5. Note your:
   - client_id (under your app name)
   - client_secret

## Detailed Guide

### Creating Your App

1. Visit Reddit's app preferences:
   - Go to https://www.reddit.com/prefs/apps
   - You must be logged in to your Reddit account

2. Create a new application:
   - Click "create another app..." at the bottom
   - This creates a new OAuth application

3. Fill in the application details:
   - **name**: Name your app (e.g., "redditarr")
   - **type**: Select "script"
   - **description**: Brief description (e.g., "Personal media archiver")
   - **redirect uri**: Use http://localhost:8481
   - These details are for your reference only

4. Click "create app" to generate your credentials

### Finding Your Credentials

After creating the app, you'll see:
- client_id: The string under "personal use script"
- client_secret: The longer string labeled "secret"

### Using Your Credentials

Depending on your setup:
- Docker Compose: Add to .env file
- UnRAID: Enter in Docker settings panel
- Other: Set as environment variables

### Security Notes

- Keep your credentials private
- Don't commit credentials to version control
- Use a dedicated Reddit account if preferred
- Regularly check Reddit's API usage dashboard

### Troubleshooting

If you get authentication errors:
- Verify credentials are copied correctly
- Ensure username/password are correct
- Check Reddit account status
- Verify no special characters in configuration

Need more help? Open an issue on GitHub.