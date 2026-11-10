"""Échantillons du tableau de bord bornés à quinze minutes."""

from collections import deque
import statistics


class LiveSeries:
    def __init__(self, capacity=900):
        self.points = deque(maxlen=capacity)
        self.last_sample = None

    def sample(self, now, timestamp, devices, scanning):
        if self.last_sample is not None and now - self.last_sample < 1:
            return None
        self.last_sample = now
        rssis = [item.rssi for item in devices if item.rssi is not None]
        point = dict(timestamp=timestamp, active=len(devices) if scanning else None,
                     mean_rssi=round(statistics.mean(rssis), 1) if scanning and rssis else None,
                     rssi_sum=sum(rssis) if scanning else 0, rssi_count=len(rssis) if scanning else 0)
        self.points.append(point)
        return point

    def snapshot(self):
        return [dict(timestamp=p['timestamp'], active=p['active'], mean_rssi=p['mean_rssi']) for p in self.points]
