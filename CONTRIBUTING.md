# Contributing to Redditarr

Thank you for your interest in Redditarr! While this is primarily a personal project that I actively maintain, I welcome community involvement in many ways.

## Quick Start

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test your changes
5. Create a pull request

## Ways to Contribute
- Report bugs and issues
- Suggest new features
- Improve documentation
- Discuss ideas in issues
- Test beta releases
- Submit pull requests for consideration

## Project Structure
- `main` branch contains stable releases
- `beta` branch contains release candidates
- `dev` branch contains active development

## Code Contributions
While I maintain primary development to ensure consistency, you're welcome to submit pull requests! Here's how:

1. Fork the repository
2. Create a feature branch from `dev`
3. Make your changes following our code style
4. Test thoroughly
5. Submit a pull request with:
   - Clear description of changes
   - Purpose/motivation
   - Any breaking changes
   - Test results

I'll review all contributions and work with you to get them integrated if they align with the project's vision.

## Development Setup

1. Clone your fork:
```bash
git clone https://github.com/martiantux/redditarr.git
```

2. Set up the development environment:
```bash
cp .env.example .env
# Edit .env with your Reddit API credentials
```

3. Run in development mode:
```bash
docker-compose up --build
```

## Guidelines

### Code Style
- Follow existing code patterns
- Use meaningful variable names
- Add comments for complex logic
- Keep functions focused and modular

### Commits
- Use clear commit messages
- One feature/fix per commit
- Reference issues where applicable
- Follow the format: `type: brief description`
  - Types: feat, fix, docs, style, refactor, test, chore

### Pull Requests
- Create PRs against the `dev` branch
- Describe your changes clearly
- Include the purpose/motivation
- Mention any breaking changes

## Testing

- Test your changes thoroughly
- Ensure rate limiting is respected
- Verify error handling
- Check cross-platform compatibility

## Need Help?

- Check existing issues
- Open a new issue for discussions
- Join the community (links coming soon)

## Areas to Contribute

- Bug fixes
- Documentation improvements
- New media host support
- UI enhancements
- Performance optimizations
- Error handling improvements

Thanks for helping make Redditarr better!
