class RobloxProfileError(RuntimeError):
    """Base error for the Roblox Studio profile."""

class ActivationError(RobloxProfileError):
    pass

class ReplayDetected(ActivationError):
    pass

class SchemaError(RobloxProfileError):
    pass

class CapabilityError(RobloxProfileError):
    pass

class BudgetError(RobloxProfileError):
    pass

class ValidationError(RobloxProfileError):
    pass

class WorkflowError(RobloxProfileError):
    pass
