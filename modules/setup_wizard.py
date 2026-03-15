"""
Setup wizard for calibrating OBS chat region coordinates

Provides interactive UI for selecting the chat region on screen.
"""

import logging
import sys
import cv2
import numpy as np
from mss import mss
from typing import Tuple, Optional

logger = logging.getLogger(__name__)


class SetupWizard:
    """Interactive calibration wizard for chat region selection"""
    
    def __init__(self):
        self.drawing = False
        self.start_point = None
        self.end_point = None
        self.screenshot = None
        self.display_image = None
    
    def _mouse_callback(self, event, x, y, flags, param):
        """Handle mouse events for region selection"""
        if event == cv2.EVENT_LBUTTONDOWN:
            self.drawing = True
            self.start_point = (x, y)
            self.end_point = (x, y)
        
        elif event == cv2.EVENT_MOUSEMOVE:
            if self.drawing:
                self.end_point = (x, y)
                # Update display with current rectangle
                self.display_image = self.screenshot.copy()
                cv2.rectangle(
                    self.display_image,
                    self.start_point,
                    self.end_point,
                    (0, 255, 0),
                    2
                )
        
        elif event == cv2.EVENT_LBUTTONUP:
            self.drawing = False
            self.end_point = (x, y)
            # Draw final rectangle
            self.display_image = self.screenshot.copy()
            cv2.rectangle(
                self.display_image,
                self.start_point,
                self.end_point,
                (0, 255, 0),
                2
            )
    
    def capture_screenshot(self) -> np.ndarray:
        """Capture full screen screenshot"""
        try:
            with mss() as sct:
                # Capture primary monitor
                monitor = sct.monitors[1]
                screenshot = sct.grab(monitor)
                
                # Convert to numpy array (BGR format for OpenCV)
                img = np.array(screenshot)
                img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
                
                logger.info(f"Captured screenshot: {img.shape[1]}x{img.shape[0]}")
                return img
        
        except Exception as e:
            logger.error(f"Failed to capture screenshot: {e}")
            raise
    
    def run_calibration(self) -> Optional[Tuple[int, int, int, int]]:
        """
        Run interactive calibration wizard
        
        Returns:
            Tuple of (x, y, width, height) or None if cancelled
        """
        print("\n" + "="*60)
        print("CHAT REGION CALIBRATION WIZARD")
        print("="*60)
        print("\nInstructions:")
        print("1. Open OBS and ensure your Twitch chat widget is visible")
        print("2. Position OBS window so chat is clearly visible")
        print("3. Press ENTER to take a screenshot")
        
        input("\nPress ENTER when ready...")
        
        # Capture screenshot
        print("Capturing screenshot...")
        self.screenshot = self.capture_screenshot()
        self.display_image = self.screenshot.copy()
        
        # Calculate display size (scale down if needed for large monitors)
        max_height = 900
        height, width = self.screenshot.shape[:2]
        scale = 1.0
        
        if height > max_height:
            scale = max_height / height
            display_width = int(width * scale)
            display_height = int(height * scale)
            display_img = cv2.resize(self.screenshot, (display_width, display_height))
            self.screenshot = display_img
            self.display_image = display_img.copy()
            logger.info(f"Scaled screenshot to {display_width}x{display_height} (scale={scale:.2f})")
        
        # Create window and set mouse callback
        window_name = "Select Chat Region - Click and drag to select, press 'c' to confirm, 'q' to quit"
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        cv2.setMouseCallback(window_name, self._mouse_callback)
        
        print("\nSelect the chat region:")
        print("  - Click and drag to draw a rectangle around the chat area")
        print("  - Press 'c' to CONFIRM selection")
        print("  - Press 'q' to QUIT without saving")
        print("  - Press 'r' to RESET selection")
        
        while True:
            cv2.imshow(window_name, self.display_image)
            key = cv2.waitKey(1) & 0xFF
            
            if key == ord('c') or key == ord('C'):
                # Confirm selection
                if self.start_point and self.end_point:
                    cv2.destroyAllWindows()
                    return self._get_region_coords(scale)
                else:
                    print("No region selected! Draw a rectangle first.")
            
            elif key == ord('q') or key == ord('Q'):
                # Quit without saving
                print("Calibration cancelled.")
                cv2.destroyAllWindows()
                return None
            
            elif key == ord('r') or key == ord('R'):
                # Reset selection
                self.start_point = None
                self.end_point = None
                self.display_image = self.screenshot.copy()
                print("Selection reset. Draw a new rectangle.")
        
        cv2.destroyAllWindows()
        return None
    
    def _get_region_coords(self, scale: float) -> Tuple[int, int, int, int]:
        """
        Calculate final region coordinates from selected points
        
        Args:
            scale: Display scale factor (1.0 if no scaling)
        
        Returns:
            Tuple of (x, y, width, height) in original screen coordinates
        """
        # Ensure start is top-left, end is bottom-right
        x1 = min(self.start_point[0], self.end_point[0])
        y1 = min(self.start_point[1], self.end_point[1])
        x2 = max(self.start_point[0], self.end_point[0])
        y2 = max(self.start_point[1], self.end_point[1])
        
        # Scale back to original coordinates if screenshot was resized
        if scale != 1.0:
            x1 = int(x1 / scale)
            y1 = int(y1 / scale)
            x2 = int(x2 / scale)
            y2 = int(y2 / scale)
        
        width = x2 - x1
        height = y2 - y1
        
        print(f"\nSelected region:")
        print(f"  Position: ({x1}, {y1})")
        print(f"  Size: {width}x{height}")
        
        return (x1, y1, width, height)
    
    def show_preview(self, x: int, y: int, width: int, height: int) -> None:
        """
        Show a preview of the selected chat region
        
        Args:
            x, y: Top-left corner coordinates
            width, height: Region dimensions
        """
        print("\nCapturing preview of selected region...")
        
        try:
            with mss() as sct:
                # Capture the specific region
                monitor = {
                    "top": y,
                    "left": x,
                    "width": width,
                    "height": height
                }
                screenshot = sct.grab(monitor)
                img = np.array(screenshot)
                img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
                
                # Display preview
                window_name = "Chat Region Preview - Press any key to close"
                cv2.imshow(window_name, img)
                print("\nPreview displayed. Press any key in the preview window to close.")
                cv2.waitKey(0)
                cv2.destroyAllWindows()
                
        except Exception as e:
            logger.error(f"Failed to show preview: {e}")
            print(f"Error showing preview: {e}")


def run_calibration_wizard() -> bool:
    """
    Run the calibration wizard and save results to config
    
    Returns:
        True if calibration was successful, False otherwise
    """
    from config.config import get_config_manager
    
    wizard = SetupWizard()
    
    # Run calibration
    result = wizard.run_calibration()
    
    if result is None:
        print("\nCalibration cancelled. Run again with --calibrate flag to retry.")
        return False
    
    x, y, width, height = result
    
    # Validate dimensions
    if width < 100 or height < 100:
        print(f"\nWarning: Selected region seems very small ({width}x{height})")
        print("Chat region should be at least 200x300 pixels for good OCR accuracy.")
        confirm = input("Continue anyway? (y/n): ")
        if confirm.lower() != 'y':
            print("Calibration cancelled.")
            return False
    
    # Show preview
    print("\nShowing preview of selected region...")
    wizard.show_preview(x, y, width, height)
    
    # Confirm save
    print("\nSave this chat region to configuration?")
    confirm = input("Enter 'y' to save, anything else to cancel: ")
    
    if confirm.lower() != 'y':
        print("Calibration cancelled.")
        return False
    
    # Save to config
    try:
        config_manager = get_config_manager()
        config_manager.update_chat_region(x, y, width, height)
        print(f"\n✓ Chat region saved to configuration!")
        print(f"  Location: {config_manager.config_path}")
        print(f"  Region: x={x}, y={y}, width={width}, height={height}")
        return True
    
    except Exception as e:
        logger.error(f"Failed to save configuration: {e}")
        print(f"\n✗ Error saving configuration: {e}")
        return False


if __name__ == "__main__":
    # Test calibration wizard
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    success = run_calibration_wizard()
    sys.exit(0 if success else 1)
