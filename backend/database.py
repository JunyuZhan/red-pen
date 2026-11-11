"""
数据库初始化和模型定义
"""

from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean, JSON, Float
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
from pathlib import Path
import sqlite3

from .config import settings

# 创建数据库引擎
engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# 数据库模型
class StageConfig(Base):
    __tablename__ = "stage_config"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), unique=True, nullable=False)
    enabled = Column(Boolean, default=True)

class SubjectConfig(Base):
    __tablename__ = "subject_config"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), nullable=False)
    stage_id = Column(Integer, nullable=False)
    enabled = Column(Boolean, default=True)

class Teacher(Base):
    __tablename__ = "teacher"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)

class Class(Base):
    __tablename__ = "class"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    teacher_id = Column(Integer, nullable=False)
    stage_id = Column(Integer, nullable=False)
    subject_id = Column(Integer, nullable=False)

class Student(Base):
    __tablename__ = "student"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    class_id = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class Assignment(Base):
    __tablename__ = "assignment"
    
    id = Column(Integer, primary_key=True, index=True)
    class_id = Column(Integer, nullable=False)
    title = Column(String(200), nullable=False)
    subject_id = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class Submission(Base):
    __tablename__ = "submission"

    id = Column(Integer, primary_key=True, index=True)
    assignment_id = Column(Integer, nullable=False)
    student_id = Column(Integer, nullable=False)
    uploader_user_id = Column(Integer, nullable=True, default=1)
    original_image_path = Column(String(500), nullable=False)
    annotated_image_path = Column(String(500), nullable=True)
    grading_summary = Column(Text, nullable=True)
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    cost_estimate = Column(Float, nullable=True)
    quality_flags = Column(JSON, nullable=True)
    quality_override = Column(Boolean, default=False)
    duration_seconds = Column(Float, nullable=True)  # 单份批改耗时（秒）
    status = Column(String(20), default="uploaded")  # uploaded, processing, completed, failed
    error_message = Column(Text, nullable=True)     # 失败原因（PRD 4.4）
    retry_count = Column(Integer, default=0)        # 已重试次数
    created_at = Column(DateTime, default=datetime.utcnow)

class AnswerTemplate(Base):
    """答案模板（PRD 4.4/M4）：同一练习册答案存一次，之后复用"""
    __tablename__ = "answer_template"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    subject = Column(String(50), nullable=False, default="数学")
    answers = Column(JSON, nullable=False, default=dict)  # {"1": "答案", ...}
    usage_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

class QuestionResult(Base):
    __tablename__ = "question_result"
    
    id = Column(Integer, primary_key=True, index=True)
    submission_id = Column(Integer, nullable=False)
    question_no = Column(Integer, nullable=False)
    question_type = Column(String(50), nullable=False)
    knowledge_point_id = Column(Integer, nullable=True)
    student_answer_text = Column(Text, nullable=True)
    correct_answer_text = Column(Text, nullable=True)
    llm_raw_output = Column(JSON, nullable=True)
    judgement_raw = Column(String(20), nullable=True)
    judgement_final = Column(String(20), nullable=True)
    error_type = Column(String(50), nullable=True)
    confidence_final = Column(Float, nullable=True)
    rule_conflict_flags = Column(JSON, nullable=True)
    teacher_override = Column(String(20), nullable=True)
    bbox_answer = Column(JSON, nullable=True)
    bbox_question = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class KnowledgePoint(Base):
    __tablename__ = "knowledge_point"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    subject_id = Column(Integer, nullable=False)
    parent_id = Column(Integer, nullable=True)

class AppConfig(Base):
    __tablename__ = "app_config"
    
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, nullable=False)
    value = Column(Text, nullable=False)

# 初始化数据库
def init_db():
    print("🗄️ 开始初始化数据库...")
    
    # 创建数据库目录
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    # 创建所有表
    Base.metadata.create_all(bind=engine)

    # 轻量迁移：为已存在的旧库补齐新增列（SQLite）
    with engine.connect() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(submission)")}
        if "error_message" not in cols:
            conn.exec_driver_sql("ALTER TABLE submission ADD COLUMN error_message TEXT")
        if "retry_count" not in cols:
            conn.exec_driver_sql("ALTER TABLE submission ADD COLUMN retry_count INTEGER DEFAULT 0")
        if "duration_seconds" not in cols:
            conn.exec_driver_sql("ALTER TABLE submission ADD COLUMN duration_seconds REAL")
        conn.commit()

    # 插入默认数据
    insert_default_data()
    
    print("✅ 数据库初始化完成")

def insert_default_data():
    db = SessionLocal()
    try:
        # 插入学段配置
        if db.query(StageConfig).count() == 0:
            stages = [
                StageConfig(name="小学", enabled=True),
                StageConfig(name="中学", enabled=False)
            ]
            db.add_all(stages)
        
        # 插入学科配置
        if db.query(SubjectConfig).count() == 0:
            subjects = [
                SubjectConfig(name="数学", stage_id=1, enabled=True),
                SubjectConfig(name="语文", stage_id=1, enabled=False),
                SubjectConfig(name="英语", stage_id=1, enabled=False),
                SubjectConfig(name="作文", stage_id=1, enabled=False)
            ]
            db.add_all(subjects)
        
        # 插入默认教师
        if db.query(Teacher).count() == 0:
            teacher = Teacher(name="默认教师")
            db.add(teacher)
            db.commit()
            
            # 获取教师ID
            teacher_id = teacher.id
            
            # 插入默认班级
            default_class = Class(
                name="默认班级",
                teacher_id=teacher_id,
                stage_id=1,
                subject_id=1
            )
            db.add(default_class)
            db.commit()
        
        # 插入默认应用配置
        if db.query(AppConfig).count() == 0:
            configs = [
                AppConfig(key="default_stage", value="小学"),
                AppConfig(key="default_subject", value="数学"),
                AppConfig(key="default_class", value="默认班级"),
                AppConfig(key="ip_address", value="192.168.1.100"),
                AppConfig(key="setup_completed", value="false")
            ]
            db.add_all(configs)
        
        db.commit()
        print("📝 默认数据插入完成")
        
    except Exception as e:
        print(f"❌ 插入默认数据失败: {e}")
        db.rollback()
    finally:
        db.close()

# 获取数据库会话
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()