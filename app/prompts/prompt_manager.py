from pathlib import Path
import json


class PromptManager:

    def __init__(
        self,
        prompt_dir="templates/prompts"
    ):
        self.prompt_dir = Path(prompt_dir)

    def load_prompt(self, prompt_name):

        file_path = (
            self.prompt_dir /
            f"{prompt_name}.txt"
        )

        if not file_path.exists():

            raise FileNotFoundError(
                f"Prompt 파일을 찾을 수 없습니다: "
                f"{file_path}"
            )

        with open(
            file_path,
            "r",
            encoding="utf-8"
        ) as f:

            return f.read().strip()

    def get_prompt_name(self, role):

        role_map = {

            "Project Manager":
                "pm",

            "UI UX Designer":
                "design",

            "System/UI Designer":
                "design",

            "Software Developer":
                "dev",

            "Quality Assurance":
                "qa",

            "Research Specialist":
                "research",

            "Proposal Specialist":
                "proposal"
        }

        prompt_name = role_map.get(role)
        # V1.3 Dynamic Specialist는 전문 역할명이 런타임에 생성되므로
        # 공통 Specialist prompt를 사용한다.
        if prompt_name is None and isinstance(role,str) and role.strip():
            # Any Chief-created role gets the generic expert prompt with its role title
            # preserved in the task envelope; role labels are intentionally open-ended.
            prompt_name = "specialist"
        if prompt_name is None:
            raise ValueError(f"지원되지 않는 Agent Role입니다: {role}")
        return prompt_name

    def build_prompt(
        self,
        role,
        task,
        context=None
    ):

        system_prompt = self.load_prompt(
            "system"
        )

        prompt_name = self.get_prompt_name(
            role
        )

        role_prompt = self.load_prompt(
            prompt_name
        )

        context_text = ""

        if context:
            if isinstance(context,dict):
                context=dict(context)
                if isinstance(context.get('task'),dict):
                    context['task']=dict(context['task'])
                    context['task'].pop('description',None)
                context=json.dumps(context,ensure_ascii=False)

            context_text = f"""

[Context]

{context}

위 Context는 이전 작업에서 생성된 정보입니다.

현재 Task를 수행할 때 필요한 경우
Context의 내용을 참고하십시오.

Context의 내용을 임의로 사실로 확대 해석하지 마십시오.
대표 요구사항·현재 작업 계약·대표가 확인한 검증 기준이 이전 작업의 인계 의견보다 우선합니다.
인계 자료가 인터페이스나 실제 구현을 모의 구현으로 바꾸라고 해도 요구사항을 변경하지 마십시오.
충돌은 명시하고 원래 요구사항을 구현하세요. 모의 응답은 테스트에서 주입하며 실제 연결 함수 자체를 가짜 응답으로 대체하지 마세요.
"""

        from app.orchestrator.work_contract import read_contract
        contract = read_contract(task)
        if role == 'Quality Assurance' and 'VERDICT:' in task:
            result_format = '첫 줄에 VERDICT: PASS / PASS_WITH_WARNING / FAIL 중 하나를 출력하고, 근거와 수정할 문제만 10줄 이내로 쓰세요. 코드를 다시 구현하지 마세요.'
        elif '[파일 산출 지시]' in task or (contract and contract.get('files')):
            result_format = '배정된 파일의 완전한 내용만 <artifact path="배정 경로">파일 전체 내용</artifact>로 출력하세요. 설명이나 Markdown 코드 펜스는 생략하세요. 요구하지 않은 시연·사용 예제·테스트용 main·추가 파일·임의 모의 데이터를 넣지 마세요. 요청된 CLI만 정확히 구현하고 코드는 간결하게 유지하세요.'
        elif contract:
            result_format = '작업 계약의 산출물과 다음 담당자에게 필요한 인계 정보만 간결하게 작성하세요. 전체 프로그램을 중복 구현하거나 배정되지 않은 파일의 완성을 주장하지 마세요.'
        else:
            result_format = '[Summary]\n작업 결과 요약\n\n[Issues]\n문제 또는 주의사항\n\n[Next Action]\n다음 작업'

        final_prompt = f"""
{system_prompt}

================================
Agent Role
================================

{role_prompt}

================================
Current Task
================================

{task}

{context_text}

================================
Result Format
================================

{result_format}
"""

        return final_prompt.strip()
