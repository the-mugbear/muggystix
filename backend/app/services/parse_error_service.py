import traceback
import os
from typing import Optional
from sqlalchemy.orm import Session

from app.db import models
from app.schemas.schemas import ParseErrorCreate

def log_parse_error(
    db: Session,
    filename: str,
    file_content: Optional[bytes] = None,
    error: Exception = None,
    error_type: str = "parsing_error",
    file_type: Optional[str] = None,
    custom_message: Optional[str] = None,
    project_id: int = None,
) -> models.ParseError:
    """
    Log a parsing error to the database for user review
    
    Args:
        db: Database session
        filename: Name of the file that failed to parse
        file_content: Raw file content (bytes) for preview generation
        error: The exception that occurred
        error_type: Type of error (parsing_error, validation_error, format_error)
        file_type: Type of file being parsed (nmap_xml, eyewitness_json, etc.)
        custom_message: Custom user-friendly message
    
    Returns:
        The created ParseError model instance
    """
    
    # Generate error details
    error_message = str(error) if error else custom_message or "Unknown parsing error"
    error_details = {}
    
    if error:
        error_details = {
            "exception_type": type(error).__name__,
            "traceback": traceback.format_exc(),
        }
        
        # Add line number context if available
        if hasattr(error, 'lineno'):
            error_details["line_number"] = error.lineno
        if hasattr(error, 'offset'):
            error_details["column_offset"] = error.offset
    
    # Generate file preview (first 1000 characters)
    file_preview = None
    file_size = None
    if file_content:
        file_size = len(file_content)
        try:
            # Try to decode as UTF-8 first
            preview_text = file_content[:1000].decode('utf-8', errors='replace')
            file_preview = preview_text
        except Exception:
            # If decode fails, use hex representation
            file_preview = f"Binary data: {file_content[:100].hex()}"
    
    # Generate user-friendly message
    user_message = _generate_user_message(error, error_type, file_type, filename)
    if custom_message:
        user_message = custom_message
    
    # Create parse error record
    parse_error = models.ParseError(
        filename=filename,
        file_type=file_type,
        file_size=file_size,
        error_type=error_type,
        error_message=error_message,
        error_details=error_details,
        file_preview=file_preview,
        user_message=user_message,
        status="unresolved",
        project_id=project_id,
    )
    
    db.add(parse_error)
    db.commit()
    db.refresh(parse_error)
    
    return parse_error

def _generate_user_message(error: Exception, error_type: str, file_type: str, filename: str) -> str:
    """Generate a user-friendly error message"""
    
    file_type_display = {
        "nmap_xml": "Nmap XML",
        "nessus_xml": "Nessus XML",
        "eyewitness_json": "Eyewitness JSON",
        "eyewitness_csv": "Eyewitness CSV",
        "masscan_xml": "Masscan XML",
        "masscan_json": "Masscan JSON",
        "masscan_list": "Masscan List"
    }.get(file_type, file_type or "scan")
    
    if error_type == "format_error":
        return (
            f"The file '{filename}' doesn't appear to be a valid {file_type_display} file. "
            f"Please verify the file format and try again."
        )
    elif error_type == "validation_error":
        return (
            f"The file '{filename}' has invalid or missing required data. "
            f"The {file_type_display} file may be corrupted or incomplete."
        )
    elif error_type == "parsing_error":
        if "xml" in file_type.lower() if file_type else "xml" in filename.lower():
            return (
                f"Failed to parse the XML file '{filename}'. The file may be malformed, "
                f"corrupted, or not a valid {file_type_display} output file."
            )
        elif "json" in file_type.lower() if file_type else "json" in filename.lower():
            return (
                f"Failed to parse the JSON file '{filename}'. The file may contain invalid JSON "
                f"syntax or not be a valid {file_type_display} output file."
            )
        else:
            return (
                f"Failed to parse the file '{filename}'. The file format may not be supported "
                f"or the file may be corrupted."
            )
    else:
        return (
            f"An error occurred while processing '{filename}'. "
            f"Please check the file format and try again."
        )


# v2.65.0 — `get_parse_suggestions` was defined here through every
# version up to v2.64.x but had no caller anywhere in backend or
# frontend.  Dropped to remove an orphan that a future maintainer
# could otherwise mistake for a public surface.  If a "parse-error
# suggestions" feature is ever scoped, build it on top of the actual
# `parse_errors` API endpoints — those already expose error_type +
# file_type, so re-deriving suggestions there is the right path.