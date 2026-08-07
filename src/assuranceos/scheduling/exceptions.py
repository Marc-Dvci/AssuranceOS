class SchedulingError(RuntimeError):
    pass


class ScheduleNotFoundError(SchedulingError):
    pass


class OccurrenceNotFoundError(SchedulingError):
    pass


class ScheduleConfigurationError(SchedulingError):
    pass


class OccurrenceStateError(SchedulingError):
    pass
