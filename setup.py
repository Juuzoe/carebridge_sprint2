from app import app
from extensions import db


def setup():
    with app.app_context():
        db.drop_all()
        db.create_all()


if __name__ == "__main__":
    setup()
