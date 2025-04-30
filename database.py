import sqlite3
import logging
from typing import List, Tuple, Optional

class Database:
    """Database handler for Kwork parser."""
    
    def __init__(self, db_path: str = "kwork_seen.db"):
        """Initialize database connection.
        
        Args:
            db_path: Path to the SQLite database file
        """
        self.db_path = db_path
        self.conn = None
        self.setup_tables()
    
    def setup_tables(self):
        """Create necessary tables if they don't exist."""
        try:
            self.conn = sqlite3.connect(self.db_path)
            cursor = self.conn.cursor()
            
            # Create seen projects table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS seen(
                    id TEXT PRIMARY KEY
                )
            """)
            
            # Create additional chat IDs table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS additional_chat_ids(
                    id INTEGER PRIMARY KEY,
                    name TEXT,
                    active BOOLEAN DEFAULT 1
                )
            """)
            
            self.conn.commit()
        except sqlite3.Error as e:
            logging.error(f"Database error: {e}")
        finally:
            if self.conn:
                self.conn.close()
    
    def is_project_seen(self, project_id: str) -> bool:
        """Check if a project has been seen before.
        
        Args:
            project_id: The ID of the project to check
            
        Returns:
            True if the project has been seen, False otherwise
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM seen WHERE id=?", (project_id,))
            result = cursor.fetchone() is not None
            conn.close()
            return result
        except sqlite3.Error as e:
            logging.error(f"Database error checking project: {e}")
            return False
    
    def mark_project_seen(self, project_id: str) -> bool:
        """Mark a project as seen.
        
        Args:
            project_id: The ID of the project to mark
            
        Returns:
            True if successful, False otherwise
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("INSERT INTO seen(id) VALUES(?)", (project_id,))
            conn.commit()
            conn.close()
            return True
        except sqlite3.Error as e:
            logging.error(f"Database error marking project: {e}")
            return False
    
    def get_additional_chat_ids(self) -> List[Tuple[int, str, bool]]:
        """Get list of additional chat IDs.
        
        Returns:
            List of tuples containing (id, name, active)
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT id, name, active FROM additional_chat_ids")
            result = cursor.fetchall()
            conn.close()
            return result
        except sqlite3.Error as e:
            logging.error(f"Database error getting chat IDs: {e}")
            return []
    
    def add_chat_id(self, chat_id: int, name: str) -> bool:
        """Add a new chat ID.
        
        Args:
            chat_id: The chat ID to add
            name: A name or description for this chat ID
            
        Returns:
            True if successful, False otherwise
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO additional_chat_ids(id, name) VALUES(?, ?)",
                (chat_id, name)
            )
            conn.commit()
            conn.close()
            return True
        except sqlite3.Error as e:
            logging.error(f"Database error adding chat ID: {e}")
            return False
    
    def remove_chat_id(self, chat_id: int) -> bool:
        """Remove a chat ID.
        
        Args:
            chat_id: The chat ID to remove
            
        Returns:
            True if successful, False otherwise
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM additional_chat_ids WHERE id=?", (chat_id,))
            conn.commit()
            conn.close()
            return True
        except sqlite3.Error as e:
            logging.error(f"Database error removing chat ID: {e}")
            return False
    
    def toggle_chat_id(self, chat_id: int, active: bool) -> bool:
        """Enable or disable a chat ID.
        
        Args:
            chat_id: The chat ID to toggle
            active: True to enable, False to disable
            
        Returns:
            True if successful, False otherwise
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE additional_chat_ids SET active=? WHERE id=?",
                (active, chat_id)
            )
            conn.commit()
            conn.close()
            return True
        except sqlite3.Error as e:
            logging.error(f"Database error toggling chat ID: {e}")
            return False