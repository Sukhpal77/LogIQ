import re
SENSITIVE=("password","authorization","apikey","secret","accesstoken","refreshtoken")
PATTERN=re.compile(r"(?i)(password|api[_-]?key|authorization|access[_-]?token|refresh[_-]?token|secret)(\s*[=:]\s*)((?:Bearer\s+)?[^\s,;&]+)")


def redact(value):
    if isinstance(value,dict):
        return {key:"[REDACTED]" if any(word in key.replace("_","").replace("-","").lower() for word in SENSITIVE) else redact(item) for key,item in value.items()}
    if isinstance(value,list):
        return [redact(item) for item in value]
    if isinstance(value,str):
        return PATTERN.sub(lambda match:match[1]+match[2]+"[REDACTED]",value)
    return value
