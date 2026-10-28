class RetryManager:

    MAX_RETRIES = 3

    def __init__(self):
        pass

    def can_retry(self, retry_count):
        """
        현재 retry_count 기준으로
        추가 Retry가 가능한지 판단한다.
        """

        return retry_count < self.MAX_RETRIES

    def get_next_retry_count(self, retry_count):
        """
        다음 Retry 횟수를 반환한다.
        """

        if not self.can_retry(retry_count):
            return retry_count

        return retry_count + 1

    def get_retry_strategy(self, retry_count):
        """
        Retry 횟수에 따라 실행 전략을 결정한다.
        """

        if retry_count == 0:
            return "SAME_EXECUTION"

        if retry_count == 1:
            return "CONTEXT_MODIFICATION"

        if retry_count == 2:
            return "STRATEGY_CHANGE"

        return "ESCALATE_TO_CHIEF"

    def get_retry_status(self, retry_count):
        """
        현재 Retry 상태를 반환한다.
        """

        if retry_count >= self.MAX_RETRIES:
            return "ESCALATE"

        return "RETRY_AVAILABLE"