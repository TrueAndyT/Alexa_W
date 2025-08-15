"""Configuration loader with validation."""
import configparser
import os
from pathlib import Path
from typing import Dict, List, Any


class ConfigLoader:
    """Load and validate configuration from INI file."""
    
    def __init__(self, config_path: str = "config/config.ini"):
        """Initialize config loader.
        
        Args:
            config_path: Path to configuration file
        """
        self.config_path = Path(config_path)
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")
        
        self.config = configparser.ConfigParser()
        self.config.read(self.config_path)
        self._validate()
    
    def _validate(self):
        """Validate configuration values."""
        # Check required sections
        required_sections = ['system', 'loader', 'logger', 'kwd', 'stt', 'llm', 'tts']
        for section in required_sections:
            if section not in self.config:
                raise ValueError(f"Missing required config section: {section}")
        
        # Validate system settings
        min_vram = self.get_int('system', 'min_vram_mb')
        if min_vram < 8000:
            raise ValueError(f"min_vram_mb must be at least 8000, got {min_vram}")
        
        # Validate ports
        ports = set()
        for service in ['loader', 'logger', 'kwd', 'stt', 'llm', 'tts']:
            port = self.get_int(service, 'port')
            if port < 5001 or port > 5006:
                raise ValueError(f"Port for {service} must be between 5001-5006, got {port}")
            if port in ports:
                raise ValueError(f"Duplicate port {port} for {service}")
            ports.add(port)
    
    def get(self, section: str, key: str, fallback: Any = None) -> str:
        """Get configuration value.
        
        Args:
            section: Config section name
            key: Config key name
            fallback: Default value if not found
            
        Returns:
            Configuration value or fallback
        """
        try:
            return self.config.get(section, key)
        except (configparser.NoSectionError, configparser.NoOptionError):
            if fallback is not None:
                return fallback
            raise
    
    def get_int(self, section: str, key: str, fallback: int = None) -> int:
        """Get integer configuration value.
        
        Args:
            section: Config section name
            key: Config key name
            fallback: Default value if not found
            
        Returns:
            Integer configuration value
        """
        value = self.get(section, key, fallback)
        if value is None:
            return None
        return int(value)
    
    def get_float(self, section: str, key: str, fallback: float = None) -> float:
        """Get float configuration value.
        
        Args:
            section: Config section name
            key: Config key name
            fallback: Default value if not found
            
        Returns:
            Float configuration value
        """
        value = self.get(section, key, fallback)
        if value is None:
            return None
        return float(value)
    
    def get_bool(self, section: str, key: str, fallback: bool = None) -> bool:
        """Get boolean configuration value.
        
        Args:
            section: Config section name
            key: Config key name
            fallback: Default value if not found
            
        Returns:
            Boolean configuration value
        """
        value = self.get(section, key, fallback)
        if value is None:
            return None
        return value.lower() in ('true', 'yes', '1', 'on')
    
    def get_list(self, section: str, key: str, separator: str = ',', 
                  fallback: List = None) -> List[str]:
        """Get list configuration value.
        
        Args:
            section: Config section name
            key: Config key name
            separator: List separator character
            fallback: Default value if not found
            
        Returns:
            List of configuration values
        """
        value = self.get(section, key, None)
        if value is None:
            return fallback if fallback is not None else []
        return [item.strip() for item in value.split(separator)]
    
    def get_dict(self, section: str) -> Dict[str, str]:
        """Get all key-value pairs from a section.
        
        Args:
            section: Config section name
            
        Returns:
            Dictionary of configuration values
        """
        if section not in self.config:
            return {}
        return dict(self.config[section])
