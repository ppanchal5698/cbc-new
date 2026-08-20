"""
Textract completion consumer (bottleneck B2).

Textract publishes job completion to SNS, SNS fans out to its own SQS queue, and
this consumer picks it up. That is the entire fix for the polling loop: the worker
that submitted the job is long gone and doing other work by the time this runs.

There is no separate logic here — the message is dispatched through the same
coordinator as everything else, so a completion is handled identically whether it
arrives on the OCR queue or (in local emulation, where SNS fan-out may not exist)
on the main queue.
"""

from pipeline.consumers.sqs_consumer import consume_forever

__all__ = ["consume_forever"]
