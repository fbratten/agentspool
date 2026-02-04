# Contributing to agentspool

Thank you for your interest in contributing to agentspool!

## Getting Started

1. Fork the repository
2. Clone your fork:
   ```bash
   git clone https://github.com/YOUR-USERNAME/agentspool.git
   cd agentspool
   ```
3. Create a virtual environment and install dependencies:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -e ".[dev]"
   ```

## Development Workflow

### Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run script integration test
python3 scripts/test_two_agents.py
```

### Code Style

- Follow PEP 8 guidelines
- Use type hints where practical
- Keep functions focused and well-documented

### Making Changes

1. Create a feature branch:
   ```bash
   git checkout -b feature/your-feature-name
   ```
2. Make your changes
3. Run tests to ensure everything passes
4. Commit with a clear message:
   ```bash
   git commit -m "feat: add your feature description"
   ```
5. Push and create a pull request

## Pull Request Guidelines

- Keep PRs focused on a single change
- Include tests for new functionality
- Update documentation if needed
- Reference any related issues

## Commit Message Format

We follow conventional commits:

- `feat:` — New feature
- `fix:` — Bug fix
- `docs:` — Documentation only
- `test:` — Adding or updating tests
- `refactor:` — Code change that neither fixes a bug nor adds a feature

## Questions?

Open an issue for discussion before starting major changes.

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
