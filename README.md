# Konkonsa API

A FastAPI-powered backend for **Konkonsa**, a social listening and trend intelligence platform. The API collects, processes, and exposes social media and online discussion data to help users discover trending topics, monitor conversations, and generate insights.

## Features

* RESTful API built with FastAPI
* User authentication and authorization
* Trend detection and analytics
* Dashboard endpoints
* Database migrations
* Modular router architecture
* Pydantic request and response validation
* Automated testing
* Environment-based configuration

## Project Structure

```text
api/
├── dashboard.py
├── database.py
├── env.example
├── main.py
├── models.py
├── requirements.txt
├── routers/
├── schemas.py
├── services/
├── migrations/
├── tests/
└── trentradar.db
```

## Requirements

* Python 3.11+
* pip
* Virtual Environment (recommended)

## Installation

Clone the repository:

```bash
git clone https://github.com/buildwithmo-dev/konkonsa.git
cd konkonsa/api
```

Create a virtual environment:

```bash
python -m venv .venv
```

Activate the virtual environment:

**macOS/Linux**

```bash
source .venv/bin/activate
```

**Windows**

```powershell
.venv\Scripts\activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

## Environment Variables

Copy the example environment file:

```bash
cp env.example .env
```

Update the `.env` file with the required configuration values.

Example:

```env
DATABASE_URL=sqlite:///trentradar.db
SECRET_KEY=your-secret-key
ACCESS_TOKEN_EXPIRE_MINUTES=60
```

## Running the Application

Start the development server:

```bash
uvicorn main:app --reload
```

The API will be available at:

* API: http://127.0.0.1:8000
* Interactive Docs (Swagger): http://127.0.0.1:8000/docs
* ReDoc: http://127.0.0.1:8000/redoc

## Running Tests

```bash
pytest
```

## Database

The project currently uses SQLite for development.

If using migrations:

```bash
alembic upgrade head
```

## API Endpoints

Example endpoints include:

* Authentication
* Dashboard
* Trends
* Analytics
* User Management

Refer to the Swagger documentation for the complete API specification.

## Development Workflow

```bash
git checkout -b feature/your-feature

git add .

git commit -m "Add your feature"

git push origin feature/your-feature
```

## Tech Stack

* FastAPI
* Pydantic
* SQLAlchemy
* Alembic
* SQLite (development)
* Pytest
* Uvicorn

## Contributing

1. Fork the repository.
2. Create a feature branch.
3. Commit your changes.
4. Push your branch.
5. Open a Pull Request.

## License

This project is licensed under the MIT License.

## Author

**Mohammed ("buildwithmo-dev")**
