# Redditarr

A self-hosted media archival and organization tool for Reddit content, focused on reliable storage and efficient content management.

> **Note**: Redditarr requires your own Reddit API credentials and respects Reddit's API terms of service and rate limits. For personal archival use only.

## Development Status

The project is currently in beta (v0.5.0). Please report any issues via GitHub Issues.

### Development Branches
- `main` - Latest stable release
- `beta` - Release candidates and testing versions
- `dev` - Active development, may be unstable

Users should use the `main` branch for stable installations. The `beta` branch is available for testing new features, while `dev` contains the latest changes and may be unstable.

## Features

### Current Features
- Subreddit monitoring and content archival
- Support for both SFW and NSFW content modes
- Text post and comment archival
- Multiple media item support per post
- Support for various media types:
  - Images (including galleries)
  - Videos
  - GIFs
  - Text posts with comments
- Support for multiple media hosts:
  - Reddit
  - Imgur
  - RedGifs
- Efficient rate-limited downloads
- Task queue system for media and comment processing
- Clean web interface for management
- Platform-specific optimizations (Linux, macOS, Raspberry Pi, Unraid)
- Configurable batch processing
- Advanced Reddit API client with proper rate limiting
- Multiple client support for improved throughput

### In Development
- Enhanced comment threading and display
- Improved download queue management
- Better progress tracking and status updates
- Advanced error handling and recovery
- Media file deduplication

### Planned Features
- Advanced filtering and sorting options
- Multi-subreddit mixed feed with normalized scoring
- User-based content views
- Enhanced metadata and statistics
- Configurable storage strategies
- Advanced download queue management
- Search functionality
- Content tagging

## Important Notes

### API Usage & Compliance
- This tool requires Reddit API credentials (see [API Setup Guide](API_SETUP.md))
- Implements proper rate limiting to respect Reddit's API guidelines
- Downloads are processed sequentially and efficiently to avoid API abuse
- Automatically handles API cooldowns and backoff periods

### Data Storage & Privacy
- All content is stored locally on your system
- You are responsible for managing stored content and respecting copyright
- No data is shared with external services
- Consider storage requirements before archiving large subreddits

## Installation

### Prerequisites
- Docker and Docker Compose
- Git
- Reddit API credentials (see [API Setup Guide](API_SETUP.md))
- Sufficient storage space for your archival needs

### Quick Start

1. Clone the repository:
```bash
git clone https://github.com/martiantux/redditarr.git
cd redditarr
```

2. Copy and configure environment file:
```bash
cp .env.example .env
```
Edit `.env` with your Reddit API credentials.

3. Choose your platform setup:

#### Standard Linux/macOS
```bash
cp docker-compose.override.example.yml docker-compose.override.yml
```
Update PUID/PGID in docker-compose.override.yml to match your user:
```bash
# Find your user/group ID
id -u  # PUID
id -g  # PGID
```

#### Apple Silicon (M-series) Macs
```bash
cp templates/docker-compose.mac.yml docker-compose.override.yml
```

#### Raspberry Pi
```bash
cp templates/docker-compose.raspberry.yml docker-compose.override.yml
```

#### Unraid
**Option 1: Docker GUI (Recommended)**
1. Copy the template file to Unraid:
   ```bash
   cp templates/unraid/redditarr.xml /boot/config/plugins/dockerMan/templates/
   ```
2. Add container through Unraid's Docker GUI using the template

**Option 2: Docker Compose**
```bash
cp templates/docker-compose.unraid.yml docker-compose.override.yml
```

4. Start Redditarr:
```bash
docker-compose up -d
```

The web interface will be available at `http://localhost:8481`

## Technical Details

### Architecture
- FastAPI backend with async support
- SQLite database for metadata storage
- Task queue system for media and comments
- Multiple Reddit API clients with rate limiting
- Modular service architecture
- Docker-based deployment
- Platform-specific optimizations

### Components
- **Reddit API Client**: Multi-client system with specialized behavior patterns
- **Download Manager**: Coordinates media downloads with priority handling
- **Task Queues**: Separate queues for media and comments with natural request patterns
- **Database Pool**: Efficient SQLite connection management
- **Web Interface**: Modern, responsive UI

### Data Storage
- Organized file structure by subreddit
- Efficient metadata storage in SQLite
- Support for large media collections
- Proper file naming and organization
- Future support for deduplication

### Archival Strategy
- Intelligent post selection (top 1000 all-time merged with current 1000 hot/popular)
- Smart comment archival (top 500 comments per post with conversation context)
- Prioritized media downloads (Reddit-hosted content first)
- Natural traffic patterns through multiple simulated clients

## Configuration

### Environment Variables
- `REDDIT_CLIENT_ID`: Your Reddit API client ID
- `REDDIT_CLIENT_SECRET`: Your Reddit API client secret
- `REDDIT_USERNAME`: Your Reddit username
- `REDDIT_PASSWORD`: Your Reddit password

### Docker Container Settings
- `PUID`: User ID for file permissions (default: 1000)
- `PGID`: Group ID for file permissions (default: 1000)
- `TZ`: Timezone (default: UTC)
- `MEMORY_LIMIT`: Container memory limit (default: 1G)
- `MEMORY_MIN`: Container memory reservation (default: 256M)

## Support and Contributions

If you find Redditarr useful, you can:
- Star the repository
- Report bugs via Issues
- Suggest features
- Support development via [GitHub Sponsors](https://github.com/sponsors/martiantux)

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidelines.

## Legal & Disclaimers

### Usage Guidelines
- This tool is for personal archival use only
- Users are responsible for complying with Reddit's Terms of Service
- Respect copyright and fair use principles
- Be mindful of storage and bandwidth usage

### Privacy
- All data is stored locally
- No analytics or tracking
- Users are responsible for their stored data
- Consider privacy implications when archiving user content

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

**Disclaimer**: This project is not affiliated with Reddit, Inc. All content downloaded through this tool remains subject to Reddit's Terms of Service and Content Policy. Users are responsible for ensuring their usage complies with all applicable terms and laws.
