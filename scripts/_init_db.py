import sys; sys.path.insert(0, 'backend'); import db; db.init_db(); print('tables:', len(db.query("SELECT name FROM sqlite_master WHERE type='table'")))
