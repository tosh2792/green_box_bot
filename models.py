import os
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text, Index
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime

DATABASE_URL = os.environ.get("DATABASE_URL")
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
Base = declarative_base()

class Product(Base):
    __tablename__ = 'products'

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    category = Column(String(50), nullable=False)
    price_per_kg = Column(Float, nullable=False)
    quantity = Column(Integer, default=0)
    is_available = Column(Boolean, default=True)
    photo_id = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class DeliverySlot(Base):
    __tablename__ = 'delivery_slots'

    id = Column(Integer, primary_key=True)
    start_hour = Column(Integer, nullable=False)
    end_hour = Column(Integer, nullable=False)
    is_active = Column(Boolean, default=True)

class Order(Base):
    __tablename__ = 'orders'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    user_name = Column(String)
    delivery_slot = Column(String)
    address = Column(String)
    phone = Column(String)
    status = Column(String, default='pending')  # pending, active, on_the_way, delivered, cancelled
    cancel_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    cancelled_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)
    on_the_way_at = Column(DateTime, nullable=True)  # Когда курьер отправился

    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")

class OrderItem(Base):
    __tablename__ = 'order_items'

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey('orders.id'))
    product_id = Column(Integer, ForeignKey('products.id'))
    product_name = Column(String(255))
    quantity = Column(Integer)
    price_per_kg = Column(Float)
    order = relationship("Order", back_populates="items")

class Cart(Base):
    __tablename__ = 'carts'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    product_id = Column(Integer, ForeignKey('products.id'))
    product_name = Column(String(255))
    quantity = Column(Integer, default=1)
    price_per_kg = Column(Float)

def init_db():
    Base.metadata.create_all(engine)
    session = Session()

    # Создание слотов доставки, если их нет
    existing_slots = session.query(DeliverySlot).count()
    if existing_slots == 0:
        for hour in range(10, 22):
            slot = DeliverySlot(start_hour=hour, end_hour=hour+1, is_active=True)
            session.add(slot)
        session.commit()

    session.close()