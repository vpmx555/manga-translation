"""Pure geometry helpers for typesetting; no translation model dependency."""
from typing import Optional, Tuple
import numpy as np


def largest_rectangle(mask: np.ndarray) -> Optional[Tuple[int, int, int, int]]:
    heights = np.zeros(mask.shape[1], dtype=np.int32)
    best_area = 0
    best_rectangle = None
    for row_index, row in enumerate(mask):
        heights = np.where(row, heights + 1, 0)
        stack = []
        for column in range(len(heights) + 1):
            height = int(heights[column]) if column < len(heights) else 0
            while stack and int(heights[stack[-1]]) > height:
                bar = stack.pop()
                rectangle_height = int(heights[bar])
                left = stack[-1] + 1 if stack else 0
                right = column
                area = rectangle_height * (right - left)
                if area > best_area:
                    best_area = area
                    best_rectangle = (
                        row_index - rectangle_height + 1,
                        left,
                        row_index + 1,
                        right,
                    )
            stack.append(column)
    return best_rectangle

