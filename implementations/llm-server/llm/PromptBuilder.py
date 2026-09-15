from RequestState import RequestState
from schema import Prompt


class PromptBuilder:
    def build(self, prompt: Prompt, state: RequestState) -> str:
        prompt_type = prompt.prompt_type
        content = prompt.prompt_text
        additional_param = prompt.additional_param

        if prompt_type == "testname":
            return (
                "[INST] As a detail-oriented developer, your task is to analyze the provided Java code and deduce a "
                "descriptive test method name. Follow these steps:\n"
                "1. Carefully read the Java code between the [CODE] tags.\n"
                "2. Identify the primary functionality or purpose of the test.\n"
                "3. Formulate a test method name that succinctly captures this functionality, adhering to lowerCamelCase "
                "conventions.\n"
                "4. Place your suggested test method name between the [TESTNAME] and [/TESTNAME] tags, ensuring it is "
                "clear and precise without additional descriptions.\n"
                "Remember, your focus is on clarity and precision. Use your expertise to provide a meaningful and "
                "appropriate name.[/INST]\n"
                f"[CODE]\n{content}\n[/CODE]\n"
            )
        elif prompt_type == "testdata":
            return (
                "[INST] As a meticulous Java developer focused on enhancing the clarity and effectiveness "
                "of a test suite. Your task is to refine the test data within a given code fragment. Your goal is to "
                "make the data more descriptive and representative of the situation being tested. \n\n"
                "Please follow these steps:\n"
                "1. Carefully review the provided code snippet.\n"
                "2. Improve the test data by changing the primitive values and Strings (such as integers, doubles, strings,"
                " chars) to more illustrative examples.\n"
                "3. Place your Improved code between the [TESTDATA] and [/TESTDATA] tags when you are done with the "
                "previous steps.\n\n"
                "The code snippet you need to refine is between the [CODE] and [/CODE] tag.[/INST]\n"
                f"[CODE]\n{content}\n[/CODE]\n\n"
            )
        elif prompt_type == "pool":
            return (
                "[INST] As a meticulous Java developer focused on testing various functionalities within Java NLP libraries.\n"
                "Your task is to create sentences to test different functionalities."
                "Please follow these steps:\n"
                "1. Carefully review the provided the class under test.\n"
                "2. Make 50 different sentences that can be test inputs suitably tests different functionalities. \n"
                "3. Please place the sentences between the [SENTENCES] and [/SENTENCES] tags.\n"
                "4. Make sure each example is between quotation marks.\n"
                "The class under test is between the [CODE] and [/CODE] tag.[/INST]\n"
                f"[CODE]\n{additional_param}\n[/CODE]\n\n"
            )

        elif prompt_type == "generate":
            return (
                "[INST] As a meticulous Java developer focused on writing unit test cases for various functionalities within Java NLP libraries.\n"
                "Your task is to write a java unit test to test this method under test."
                "Please follow these steps:\n"
                "1. Carefully review the provided code snippet. The generated test must be between the [TEST] and [/TEST] tags\n"
                "2. Place the unit test method in the [TEST] and [/TEST] tags when you are done.\n"
                "the code snippet you need to write a test is between the [CODE] and [/CODE].[/INST]\n"
                f"[CODE]\n{content}\n[/CODE]\n"
            )

        elif prompt_type == "codamosa":
            return (
                "# Write only one JUnit test case (in JUnit4) in java 11 for the given method under test\n."
                "### Guidelines:\n"
                "1. Each test case must be fully self-contained—do not use any loops (e.g., for, while , etc.).\n"
                "2. Avoid defining helper or external methods—place all logic directly within the test case body.\n"
                "3. The test case should be started with @Test\n"
                "### Code to Test:\n"
                "[CODE]\n"
                f"{content}\n"
                "[/CODE]\n"
            )

        elif prompt_type == "generate_test_class":
            # return (
            #     "[INST] As a meticulous Java developer focused on writing unit test cases for various functionalities within Java NLP libraries.\n"
            #     "Your task is to write a java unit tests to test this class under test."
            #     "Please follow these steps:\n"
            #     "1. Carefully review the provided code snippet.\n"
            #     "2. Each Test Case shouldn't have any loop statements such as for and while.\n"
            #     "3. Do not define any external methods, and put every things in the body of each test case.\n"
            #     "4. Do not use of @Before and put test setup in the body of each test case.\n"
            #     "5. Do not use private fields or fields directly.\n"
            #     "6. Write the test cases in format of JUnit 4.\n"
            #     "the code snippet you need to write a test is between the [CODE] and [/CODE].[/INST]\n"
            #     f"[CODE]\n{content}\n[/CODE]\n"
            # )
            return (
                "You are an expert Java developer specializing in writing high-quality unit tests for NLP libraries. "
                "Your task is to generate a full Java test class includes a large set of JUnit 4 test cases in Java 11 for the given class while strictly following the guidelines below:\n\n"
                "### Guidelines:\n"
                "1. Review the provided code snippet carefully before writing the test cases.\n"
                "2. Ensure comprehensive test coverage, including edge cases and potential failure scenarios.\n"
                "3. The test cases include complex test setups simulating various scenarios using multiple calls from different classes and methods.\n"
                "### Code to Test:\n"
                "[CODE]\n"
                f"{content}\n"
                "[/CODE]\n"
            )

        elif prompt_type == "generate_test_class_wosr":
            return (
                "You are an expert Java developer specializing in writing high-quality unit tests for NLP libraries. "
                "Your task is to generate a full Java test class includes a large set of JUnit 4 test cases in Java 11 for the given class while strictly following the guidelines below:\n\n"
                "### Guidelines:\n"
                "1. Review the provided code snippet carefully before writing the test cases.\n"
                "2. Ensure comprehensive test coverage, including edge cases and potential failure scenarios.\n"
                "3. The test cases include complex test setups simulating various scenarios using multiple calls from different classes and methods.\n"
                "4. Each test case must be fully self-contained—do not use any loops (e.g., for, while , etc.).\n"
                "5. Avoid defining helper or external methods—place all logic directly within the test case body.\n"
                "6. Do not use @Before or any setup annotations—initialize all required data within each individual test case.\n"
                "7. Do not access private fields of the class under test.\n"
                "### Code to Test:\n"
                "[CODE]\n"
                f"{content}\n"
                "[/CODE]\n"
            )

        elif prompt_type == "correct_test_class":
            return (
                "Revise the following test cases according to these strict guidelines:\n"
                "1. Each test case must be fully self-contained—do not use any loops (e.g., for, while , etc.).\n"
                "2. Avoid defining helper or external methods—place all logic directly within the test case body.\n"
                "3. Do not use @Before or any setup annotations—initialize all required data within each individual test case.\n"
                "4. Do not access private fields of the class under test.\n"
            )

        elif prompt_type == "self_refinement":
            return (
                "Please review the existing test cases and identify any missing edge cases or scenarios. "
                "Your task is to enhance test coverage by adding additional test cases where necessary.\n\n"
                "### Guidelines:\n"
                "1. Analyze the current test cases to find untested scenarios.\n"
                "2. Add new test cases to improve overall coverage, branch and line coverages, focusing on edge cases, boundary conditions, and failure scenarios.\n"
                "3. Place all logic directly within the test case body. Avoid defining helper methods.\n"
                "Provide only the bunch of additional test cases without rewriting existing ones. all logic directly within the test case body"
            )

        elif prompt_type == "test_suite":
            return (
                "[INST] As a meticulous Java developer focused on writing unit test cases for various functionalities within Java NLP libraries.\n"
                "Your task is to write java unit tests to test this class under test."
                "Please follow these steps:\n"
                "1. Carefully review the provided code snippet. The generated tests must be between the [TEST] and [/TEST] tags\n"
                "2. Place the unit test method in the [TEST] and [/TEST] tags when you are done.\n"
                "the code snippet you need to write a test is between the [CODE] and [/CODE].[/INST]\n"
                f"[CODE]\n{content}\n[/CODE]\n"
            )

        elif prompt_type == "integration_merge":
            # return (
            #     "[INST] You are a meticulous Java developer specializing in writing high-quality unit tests.\n"
            #     "Your task is to merge automatically generated test cases with manually written ones.\n\n"
            #     "Please follow these instructions carefully:\n"
            #     "1. Review the automatically generated tests (between [AGT] and [/AGT]) and the manually written tests (between [MWT] and [/MWT]).\n"
            #     "2. Produce the merged version of the tests between the [TEST] and [/TEST] tags.\n"
            #     "3. Ensure the merged tests strictly follow the coding style and conventions of the manually written tests.\n"
            #     "4. Reuse relevant private methods, fields, and setup code from the manually written tests whenever beneficial.\n"
            #     "5. Replace or remove any unnecessary dependencies introduced by the automatically generated tests.\n"
            #     "6. Maintain correctness, readability, and consistency throughout the integrated test file.\n\n"
            #     "Output only the final merged version between [TEST] and [/TEST] tags.\n"
            #     "[/INST]\n"
            #     f"[MWT]\n{additional_param}\n[/MWT]\n"
            #     f"[AGT]\n{content}\n[/AGT]\n"
            # )

            return (
                "[INST] You are an experienced Java developer specializing in unit testing.\n"
                "You are given:\n"
                "1) Manually written tests (MWT), which define the target style, structure, helpers, and setup.\n"
                "2) Improved generated tests (IGT), which must be adapted and merged.\n"
                "Your task is to adapt IGT to match the MWT style and merge them into a single coherent Java test class.\n\n"

                "### Requirements (must follow exactly):\n"
                "1) Reuse existing private helper methods, fields, constants, and test fixtures defined in MWT whenever applicable.\n"
                "2) Reuse the existing test setup from MWT (e.g., @Before, @BeforeEach, shared initialization logic).\n"
                "3) You MAY rename identifiers and adjust formatting/comments in IGT to match MWT style.\n"
                "4) Produce ONE merged Java test file that compiles.\n"
                "5) Keep the IGT test logic as much as possible.\n"

                "### Input format:\n"
                "- MWT is between [MWT] and [/MWT].\n"
                "- IGT is between [IGT] and [/IGT].\n\n"

                "### Output format:\n"
                "- Output ONLY the full merged Java test code.\n"
                "- No explanations or commentary.\n\n"

                "[/INST]\n"
                f"[MWT]\n{content}\n[/MWT]\n"
                f"[IGT]\n{additional_param}\n[/IGT]\n"
            )

        elif prompt_type == "integration_step_style" or prompt_type == "integration_step_by_step":
            return (
                "[INST] You are an experienced Java developer specializing in unit testing.\n"
                "You are given:\n"
                "1) Manually written tests (MWT), which define the target style and conventions.\n"
                "2) Improved generated tests (IGT), which must be adapted and merged.\n"
                "Your task: adapt IGT to the MWT coding style and merge them into a single coherent Java test class.\n\n"

                "### Step 1 - Adopt coding styles (must follow exactly):\n"
                "1) Align test method names with MWT naming conventions.\n"
                "2) Align assertions with MWT style (ordering, matcher usage, readability).\n"
                "3) Align general formatting and structure (spacing, blank lines, block ordering).\n"
                "4) Align mock usage patterns with MWT, like using Given/When/Then structure when present.\n"
                "5) Preserve IGT test logic as much as possible while adapting style.\n\n"

                "### Input format:\n"
                "- MWT is between [MWT] and [/MWT].\n"
                "- IGT is between [IGT] and [/IGT].\n\n"

                "### Output format:\n"
                "- Output ONLY the full merged Java test code.\n"
                "- No explanations or commentary.\n\n"

                "[/INST]\n"
                f"[MWT]\n{content}\n[/MWT]\n"
                f"[IGT]\n{additional_param}\n[/IGT]\n"
            )

        elif prompt_type == "integration_step_reuse":
            return (
                "[INST] You are an experienced Java developer specializing in unit testing.\n"
                "You are given:\n"
                "1) Manually written tests (MWT), which define the target helpers and fixtures.\n"
                "2) Improved generated tests (IGT), which must be adapted and merged.\n"
                "Your task: reuse MWT private methods/fields/classes and merge IGT into MWT.\n\n"

                "### Step 2 - Reuse private methods/fields/classes (must follow exactly):\n"
                "1) Reuse existing private helper methods, fields, constants, and inner classes from MWT whenever applicable.\n"
                "2) Replace duplicate or similar logic in IGT with calls to MWT helpers.\n"
                "3) Do not introduce new private helpers if an equivalent exists in MWT.\n"
                "4) Preserve IGT test intent and behavior while refactoring to reuse MWT internals.\n\n"

                "### Input format:\n"
                "- MWT is between [MWT] and [/MWT].\n"
                "- IGT is between [IGT] and [/IGT].\n\n"

                "### Output format:\n"
                "- Output ONLY the full merged Java test code.\n"
                "- No explanations or commentary.\n\n"

                "[/INST]\n"
                f"[MWT]\n{content}\n[/MWT]\n"
                f"[IGT]\n{additional_param}\n[/IGT]\n"
            )

        elif prompt_type == "integration_step_setup":
            return (
                "[INST] You are an experienced Java developer specializing in unit testing.\n"
                "You are given:\n"
                "1) Manually written tests (MWT), which define the preferred setup/teardown pattern.\n"
                "2) Improved generated tests (IGT), which must be adapted and merged.\n"
                "Your task: align test setup between IGT and MWT and merge them.\n\n"

                "### Step 3 - Test setup (must follow exactly):\n"
                "1) Reuse MWT setup/teardown hooks (e.g., @Before, @After, @BeforeEach) where applicable.\n"
                "2) Move IGT initialization into MWT setup when it is shared across tests.\n"
                "3) Avoid duplicating setup logic inside test methods if MWT uses shared setup.\n"
                "4) Preserve IGT test behavior and order of initialization.\n\n"

                "### Input format:\n"
                "- MWT is between [MWT] and [/MWT].\n"
                "- IGT is between [IGT] and [/IGT].\n\n"

                "### Output format:\n"
                "- Output ONLY the full merged Java test code.\n"
                "- No explanations or commentary.\n\n"

                "[/INST]\n"
                f"[MWT]\n{content}\n[/MWT]\n"
                f"[IGT]\n{additional_param}\n[/IGT]\n"
            )

        elif prompt_type == "integration_step_imports":
            return (
                "[INST] You are an experienced Java developer specializing in unit testing.\n"
                "You are given:\n"
                "1) Manually written tests (MWT), which define the preferred dependencies.\n"
                "2) Improved generated tests (IGT), which must be adapted and merged.\n"
                "Your task: keep imports compatible with MWT and minimize new dependencies from IGT.\n\n"

                "### Step 4 - Imports and dependencies (must follow exactly):\n"
                "1) Prefer MWT imports and dependency choices when equivalents exist.\n"
                "2) Replace IGT-only assertions or utilities with MWT-compatible alternatives when possible.\n"
                "3) Remove unused imports and avoid introducing new dependencies unless strictly required.\n"
                "4) Ensure the merged class compiles with the minimal set of imports.\n\n"

                "### Input format:\n"
                "- MWT is between [MWT] and [/MWT].\n"
                "- IGT is between [IGT] and [/IGT].\n\n"

                "### Output format:\n"
                "- Output ONLY the full merged Java test code.\n"
                "- No explanations or commentary.\n\n"

                "[/INST]\n"
                f"[MWT]\n{content}\n[/MWT]\n"
                f"[IGT]\n{additional_param}\n[/IGT]\n"
            )

        elif prompt_type == "integration_improvement" or prompt_type == "integration_all":
            return (
                "[INST] You are a meticulous Java developer who refactors auto-generated unit tests for readability.\n"
                + "Your job: rewrite the test code to improve *understandability* by renaming test methods, local variables, while keeping the test logic and behavior exactly the same.\n\n"
                    "### Rules (must follow):\n"
                + "1) Preserve behavior: do not change control flow, method calls.\n"
                + "2) Keep every test case present in the input. Do not omit any code.\n"
                + "3) Use natural, descriptive names, and avoid generic names like tmp, var1, test0.\n"
                + "4) Input tests are between [AGT] and [/AGT]. Output must be the full updated code.\n\n"
                + "### Output format:\n"
                + "- Output ONLY the rewritten Java code. No explanations.\n\n"
                "### Input:\n"
                "[/INST]\n"
                f"[AGT]\n{content}\n[/AGT]\n"
            )

        else:
            if state.get_iteration() > 3:
                return (
                    "[INST] <<SYS>> You are a Java developer optimizing JUnit tests for clarity. <</SYS>> Your task "
                    "is to make a previously written JUnit test more understandable. The returned understandable test "
                    "must be between the [TEST] and [/TEST] tags. \n"
                    f"Add comments to the code which explain what is happening and the "
                    "intentions of what is being done."
                    "Overall, it is the goal to have a more descriptive test.\n "
                    "The previously written test to improve is between the [CODE] and [/CODE] tags.\n"
                    f"[CODE]\n{content}\n[/CODE]\n")
            else:
                given_when_then = " with the Given, When, Then Structure" if state.is_first_run() else ""
                return (
                    "[INST] <<SYS>> You are a Java developer optimizing JUnit tests for clarity. <</SYS>> Your task "
                    "is to make a previously written JUnit test more understandable. The returned understandable test "
                    "must be between the [TEST] and [/TEST] tags. \n"
                    f"Add comments{given_when_then} to the code which explain what is happening and the "
                    "intentions of what is being done."
                    "Only Change variable names to make them more relevant leaving the test data untouched."
                    "Overall, it is the goal to have a more concise test which is "
                    "both descriptive as well as relevant to the context. \n"
                    "The previously written test to improve is between the [CODE] and [/CODE] tags.\n"
                    f"[CODE]\n{content}\n[/CODE]\n")
