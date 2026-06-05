"""Mathematical utility functions."""

from typing import Union


def clamp(
    value: Union[int, float],
    min_val: Union[int, float],
    max_val: Union[int, float]
) -> Union[int, float]:
    """
    Clamp a value between a minimum and maximum value.
    
    Returns the value clamped to the range [min_val, max_val].
    If value is less than min_val, returns min_val.
    If value is greater than max_val, returns max_val.
    Otherwise, returns value unchanged.
    
    Args:
        value: The value to clamp.
        min_val: The minimum allowed value.
        max_val: The maximum allowed value.
        
    Returns:
        The clamped value.
        
    Raises:
        ValueError: If min_val > max_val.
    """
    if min_val > max_val:
        raise ValueError(f"min_val ({min_val}) cannot be greater than max_val ({max_val})")
    
    return max(min_val, min(value, max_val))


if __name__ == "__main__":
    # Test basic clamping
    assert clamp(5, 0, 10) == 5, "Value within range should be unchanged"
    
    # Test minimum clamping
    assert clamp(-5, 0, 10) == 0, "Value below range should be clamped to min_val"
    
    # Test maximum clamping
    assert clamp(15, 0, 10) == 10, "Value above range should be clamped to max_val"
    
    # Test with floats
    assert clamp(3.7, 2.5, 4.5) == 3.7, "Float value within range should be unchanged"
    assert clamp(1.5, 2.5, 4.5) == 2.5, "Float value below range should be clamped to min_val"
    assert clamp(5.5, 2.5, 4.5) == 4.5, "Float value above range should be clamped to max_val"
    
    # Test with equal min and max
    assert clamp(10, 5, 5) == 5, "When min_val == max_val, should return that value"
    
    # Test edge case
    assert clamp(0, 0, 0) == 0, "All parameters equal should return that value"
    
    print("All clamp() function tests passed!")
