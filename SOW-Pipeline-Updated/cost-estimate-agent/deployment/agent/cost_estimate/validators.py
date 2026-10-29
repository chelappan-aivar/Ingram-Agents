"""Structure check for the estimate JSON the agent returns (unchanged from agent/utils/validators.py)."""


def validate_cost_data(cost_data):
    """
    Validate cost estimate data structure
    
    Args:
        cost_data: dict with estimate data
        
    Returns:
        bool: True if valid
        
    Raises:
        ValueError: If data is invalid
    """
    required_fields = ['estimate_name', 'estimate_id', 'calculator_url', 'region']
    
    for field in required_fields:
        if field not in cost_data:
            raise ValueError(f"Missing required field: {field}")
    
    # Validate environments exist
    if 'environments' not in cost_data or not cost_data['environments']:
        raise ValueError("No environments found in estimate data")
    
    # Validate each environment has services
    for env_name, env_data in cost_data['environments'].items():
        if 'services' not in env_data:
            raise ValueError(f"Environment '{env_name}' has no services")
        
        if not env_data['services']:
            raise ValueError(f"Environment '{env_name}' has empty services list")
    
    return True


def validate_estimate_id(estimate_id):
    """
    Validate estimate ID format
    
    Args:
        estimate_id: str estimate identifier
        
    Returns:
        bool: True if valid
    """
    if not estimate_id or len(estimate_id) < 10:
        raise ValueError("Invalid estimate ID")
    
    return True
