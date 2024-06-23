#
# Copyright 2023 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

"""Chatbot web service for Docs Agent"""

from flask import Blueprint, render_template, request, redirect, url_for, json, jsonify
import markdown
import markdown.extensions.fenced_code
import urllib
import os
import typing
from datetime import datetime
from absl import logging
import pytz
import uuid
import re

from docs_agent.utilities.helpers import (
    parse_related_questions_response_to_html_list,
    trim_section_for_page_link,
    named_link_html,
    md_to_html,
)
from docs_agent.utilities import config
from docs_agent.preprocess.splitters import markdown_splitter
from docs_agent.postprocess.docs_retriever import SectionProbability

from docs_agent.storage.chroma import Format
from docs_agent.agents.docs_agent import DocsAgent

from docs_agent.memory.logging import log_question, log_like


# This is used to define the app blueprint using a productConfig
def construct_blueprint(
    product_config: config.ProductConfig, app_mode: typing.Optional[str] = None
):
    bp = Blueprint("chatui", __name__)
    if product_config.db_type == "google_semantic_retriever":
        if product_config.secondary_db_type == "chroma":
            docs_agent = DocsAgent(config=product_config, init_chroma=True)
        else:
            # A local Chroma DB is not needed for the Semantic Retreiver only mode.
            docs_agent = DocsAgent(config=product_config, init_chroma=False)
    elif product_config.db_type == "none":
        docs_agent = DocsAgent(config=product_config, init_chroma=False, init_semantic=False)
    else:
        docs_agent = DocsAgent(config=product_config, init_chroma=True)
    logging.info(
        f"Launching the Flask app for product: {product_config.product_name} with app_mode: {app_mode}"
    )
    # Assign templates and redirects
    if app_mode == "web":
        app_template = "chatui/index.html"
        redirect_index = "chatui.index"
    elif app_mode == "experimental":
        app_template = "chatui-experimental/index.html"
        redirect_index = "chatui-experimental.index"
    elif app_mode == "widget":
        app_template = "chat-widget/index.html"
        redirect_index = "chat-widget.index"
    elif app_mode == "1.5":
        app_template = "chatui-1.5/index.html"
        redirect_index = "chatui-1.5.index"
    else:
        app_template = "chatui/index.html"
        redirect_index = "chatui.index"

    @bp.route("/", methods=["GET", "POST"])
    def index():
        server_url = request.url_root.replace("http", "https")
        return render_template(
            app_template,
            product=product_config.product_name,
            server_url=server_url,
        )

    @bp.route("/api/ask-docs-agent", methods=["GET", "POST"])
    def api():
        try:
            input = request.get_json()
            if input["question"]:
                (
                    full_prompt,
                    response,
                    context,
                    search_result,
                ) = ask_model_with_sources(input["question"], agent=docs_agent)
                source_array = []
                # for source in search_result:
                #     source_array.append(source.returnDictionary())
                dictionary = {
                    "response": response,
                    "full_prompt": full_prompt,
                    "sources": source_array,
                }
                return jsonify(dictionary)
            else:
                error = "Must have a valid question key in your JSON"
                return jsonify({"error": error}), 400
        except:
            error = "Must be a valid JSON"
            return jsonify({"error": error}), 400

    @bp.route("/like", methods=["GET", "POST"])
    def like():
        if request.method == "POST":
            json_data = json.loads(request.data)
            is_like = json_data.get("like")
            uuid_found = json_data.get("uuid")
            log_like(is_like, str(uuid_found).strip())
            return "OK"
        else:
            return redirect(url_for(redirect_index))

    @bp.route("/rewrite", methods=["GET", "POST"])
    def rewrite():
        # Create the 'rewrites' directory if it does not exist.
        rewrites_dir = "rewrites"
        is_exist = os.path.exists(rewrites_dir)
        if not is_exist:
            os.makedirs(rewrites_dir)
        if request.method == "POST":
            json_data = json.loads(request.data)
            user_id = json_data.get("user_id")
            question_captured = json_data.get("question")
            original_response = json_data.get("original_response")
            rewrite_captured = json_data.get("rewrite")
            date_format = "%m%d%Y-%H%M%S"
            date = datetime.now(tz=pytz.utc)
            date = date.astimezone(pytz.timezone("US/Pacific"))
            print(
                "[" + date.strftime(date_format) + "] A user has submitted a rewrite."
            )
            print("Submitted by: " + user_id + "\n")
            print("# " + question_captured.strip() + "\n")
            print("## Original response\n")
            print(original_response.strip() + "\n")
            print("## Rewrite\n")
            print(rewrite_captured + "\n")
            filename = (
                rewrites_dir
                + "/"
                + question_captured.strip()
                .replace(" ", "-")
                .replace("?", "")
                .replace("'", "")
                .lower()
                + "-"
                + date.strftime(date_format)
                + ".md"
            )
            with open(filename, "w", encoding="utf-8") as file:
                file.write("Submitted by: " + user_id + "\n\n")
                file.write("# " + question_captured.strip() + "\n\n")
                file.write("## Original response\n\n")
                file.write(original_response.strip() + "\n\n")
                file.write("## Rewrite\n\n")
                file.write(rewrite_captured + "\n")
                file.close()
            return "OK"
        else:
            return redirect(url_for(redirect_index))

    @bp.route("/feedback", methods=["GET", "POST"])
    def feedback():
        # Create the 'feedback' directory if it does not exist.
        feedback_dir = "feedback"
        is_exist = os.path.exists(feedback_dir)
        if not is_exist:
            os.makedirs(feedback_dir)
        if request.method == "POST":
            json_data = json.loads(request.data)
            user_id = json_data.get("user_id")
            question = json_data.get("question")
            response = json_data.get("response")
            feedback = json_data.get("feedback")
            date_format = "%m%d%Y-%H%M%S"
            date = datetime.now(tz=pytz.utc)
            date = date.astimezone(pytz.timezone("US/Pacific"))
            print(
                "[" + date.strftime(date_format) + "] A user has submitted feedback."
            )
            print("Submitted by: " + user_id + "\n")
            print("# " + question.strip() + "\n")
            print("## Response\n")
            print(response.strip() + "\n")
            print("## Feedback\n")
            print(feedback + "\n")
            filename = (
                feedback_dir
                + "/"
                + question.strip()
                .replace(" ", "-")
                .replace("?", "")
                .replace("'", "")
                .lower()
                + "-"
                + date.strftime(date_format)
                + ".md"
            )
            with open(filename, "w", encoding="utf-8") as file:
                file.write("Submitted by: " + user_id + "\n\n")
                file.write("# " + question.strip() + "\n\n")
                file.write("## Response\n\n")
                file.write(response.strip() + "\n\n")
                file.write("## Feedback\n\n")
                file.write(feedback + "\n")
                file.close()
            return "OK"
        else:
            return redirect(url_for(redirect_index))

    # Render a response page when the user asks a question
    # using input text box.
    @bp.route("/result", methods=["GET", "POST"])
    def result():
        if request.method == "POST":
            question = request.form["question"]
            return ask_model(question, agent=docs_agent, template=app_template)
        else:
            return redirect(url_for(redirect_index))

    # Render a response page when the user clicks a question
    # from the related questions list.
    @bp.route("/question/<ask>", methods=["GET", "POST"])
    def question(ask):
        if request.method == "GET":
            question = urllib.parse.unquote_plus(ask)
            return ask_model(question, agent=docs_agent, template=app_template)
        else:
            return redirect(url_for(redirect_index))

    # Render the log view page
    @bp.route("/logs", methods=["GET", "POST"])
    def logs():
        return show_logs(agent=docs_agent)

    return bp


# Go through the `seatch_result` object returned from the AQA model
# and extract context.
def extract_context_from_search_result(search_result):
    context = ""
    context_count = 0
    for item in search_result:
        context_count += 1
        # Add a "Reference[#]" line at the end of each context.
        context += item.section.content + "\nReference [" + str(context_count) + "]\n\n"
    context = context.strip()
    return context


# Construct a set of prompts using the user question, send the prompts to
# the lanaguage model, receive responses, and present them into a page.
# Use template to specify a custom template for the classic web UI
def ask_model(question, agent, template: str = "chatui/index.html"):
    # Returns a built context, a total token count of the context and an array
    # of sourceOBJ
    full_prompt = ""
    final_context = ""
    docs_agent = agent
    new_question_count = 5
    results_num = 5
    aqa_response_in_html = ""

    # Debugging feature: Do not log this question if it ends with `?do_not_log`.
    can_be_logged = "True"
    question_match = re.search(r"^(.*)\?do_not_log$", question)
    if question_match:
        # Update the question to remove `do_not_log`.
        question = question_match[1] + "?"
        can_be_logged = "False"

    # Retrieve context and ask the question.
    if "gemini" in docs_agent.config.models.language_model:
        # For the `gemini-*` model
        if docs_agent.config.docs_agent_config == "experimental":
            results_num = 10
            new_question_count = 5
        else:
            results_num = 5
            new_question_count = 5
        # Note: Error if max_sources > results_num, so leave the same for now.
        if docs_agent.config.db_type == "none":
            search_result = []
            final_context = ""
            # response = ask_content_model_with_context(context="", question=question)
            # Issue if max_sources > results_num, so leave the same for now
        else:
            search_result, final_context = docs_agent.query_vector_store_to_build(
                question=question,
                token_limit=30000,
                results_num=results_num,
                max_sources=results_num,
            )
        try:
            response, full_prompt = docs_agent.ask_content_model_with_context_prompt(
                context=final_context, question=question
            )
            aqa_response_in_html = ""
        except:
            logging.error("Failed to ask content model with context prompt.")
    elif "aqa" in docs_agent.config.models.language_model:
        # For the AQA model
        if docs_agent.config.db_type == "chroma":
            (
                response,
                search_result,
            ) = docs_agent.ask_aqa_model_using_local_vector_store(
                question=question, results_num=results_num
            )
        else:
            (response, search_result) = docs_agent.ask_aqa_model_using_corpora(
                question=question
            )
        # Extract context from this AQA model's response.
        final_context = extract_context_from_search_result(search_result)
        # Save this AQA model's response.
        aqa_response_json = docs_agent.get_saved_aqa_response_json()
        # Convert this AQA model's response to HTML for better rendering.
        if aqa_response_json:
            aqa_response_in_html = json.dumps(
                type(aqa_response_json).to_dict(aqa_response_json), indent=2
            )

    ### Check the AQA model's answerable_probability field
    probability = "None"
    if docs_agent.check_if_aqa_is_used():
        aqa_response = docs_agent.get_saved_aqa_response_json()
        try:
            probability = aqa_response.answerable_probability
        except:
            probability = 0.0

    # For the 1.5 mode, retrieve additional context from the secondary knowledge database.
    additional_context = ""
    if docs_agent.config.app_mode == "1.5":
        if docs_agent.config.secondary_db_type == "chroma":
            (
                additional_search_result,
                additional_context,
            ) = docs_agent.query_vector_store_to_build(
                question=question,
                token_limit=30000,
                results_num=5,
                max_sources=5,
            )
            # Extract context from this search result.
            additional_context = extract_context_from_search_result(
                additional_search_result
            )
        elif docs_agent.config.secondary_db_type == "google_semantic_retriever":
            (
                additional_response,
                additional_search_result,
            ) = docs_agent.ask_aqa_model_using_corpora(
                question=question,
                corpus_name=str(docs_agent.config.secondary_corpus_name),
            )
            # Extract context from this search result.
            additional_context = extract_context_from_search_result(
                additional_search_result
            )

    ### PROMPT: GET RELATED QUESTIONS.
    # 1. Use the response from Prompt 1 as context and add a custom condition.
    # 2. Prepare a new question asking the model to come up with 5 related questions.
    # 3. Ask the language model with the new question.
    # 4. Parse the model's response into a list in HTML format.
    new_condition = f"Read the context below and answer the question at the end:"
    new_question = f"Can you think of {new_question_count} questions whose answers can be found in the context above?"
    try:
        (
            related_questions_response,
            new_prompt_questions,
        ) = docs_agent.ask_content_model_with_context_prompt(
            context=final_context,
            question=new_question,
            prompt=new_condition,
            model="gemini-pro",
        )
        # Clean up the response to a proper html list
        related_questions = parse_related_questions_response_to_html_list(
            markdown.markdown(related_questions_response)
        )
    except:
        related_questions = ""
        logging.error("Failed to ask content model with context prompt.")

    ### PREPARE OTHER ELEMENTS NEEDED BY UI.
    # - Create a uuid for this request.
    # - A workaround to get the server's URL to work with the rewrite and like features.
    new_uuid = uuid.uuid1()
    server_url = request.url_root.replace("http", "https")

    ### The code below is added for the new Gemini 1.5 model.
    # Ask the Gemini 1.5 model to generate a full summary.
    if docs_agent.config.app_mode == "1.5" and docs_agent.config.db_type != "none":
        if additional_context != "":
            extended_context = f"RELEVANT CONTEXT FOUND IN DOCUMENTATION:\n\n{additional_context}\n\nRELEVANT CONVERSATIONS:\n\n{final_context}\n"
        else:
            extended_context = f"{final_context}\n"
        additional_condition = (
            "DO NOT INCLUDE THE NAMES OF PEOPLE FOUND IN CONVERSATIONS"
        )
        new_condition = f"Read the context below and provide a detailed overview to address the question at the end ({additional_condition}):"
        (
            summary_response,
            summary_prompt,
        ) = docs_agent.ask_content_model_with_context_prompt(
            context=extended_context,
            question=question,
            prompt=new_condition,
            model="gemini-1.5-pro",
        )
        log_lines = f"{response}\n\n{summary_response}"
    else:
        summary_response = ""
        log_lines = f"{response}"
 
    ### LOG THIS REQUEST.
    if docs_agent.config.enable_logs_to_markdown == "True":
        log_question(
            new_uuid,
            question,
            log_lines,
            probability,
            save=can_be_logged,
            logs_to_markdown="True",
        )
    else:
        log_question(new_uuid, question, log_lines, probability, save=can_be_logged)

    ### Check the feedback mode in the `config.yaml` file.
    feedback_mode = "feedback"
    if hasattr(docs_agent.config, "feedback_mode") and  docs_agent.config.feedback_mode == "rewrite":
        feedback_mode = "rewrite"

    return render_template(
        template,
        question=question,
        response=response,
        related_questions=related_questions,
        product=docs_agent.config.product_name,
        server_url=server_url,
        uuid=new_uuid,
        aqa_response_in_html=aqa_response_in_html,
        named_link_html=named_link_html,
        trim_section_for_page_link=trim_section_for_page_link,
        md_to_html=md_to_html,
        final_context=final_context,
        search_result=search_result,
        summary_response=summary_response,
        feedback_mode=feedback_mode,
    )


# Not fully implemented
# This method is used for the API endpoint, so it returns values that can be
# packaged as JSON
def ask_model_with_sources(question, agent):
    docs_agent = agent
    full_prompt = ""
    search_result, context = docs_agent.query_vector_store_to_build(
        question=question, token_limit=30000, results_num=10, max_sources=10
    )
    context_with_instruction = docs_agent.add_instruction_to_context(context)
    if "gemini" in docs_agent.get_language_model_name():
        response, full_prompt = docs_agent.ask_content_model_with_context_prompt(
            context=context, question=question
        )
    else:
        response = docs_agent.ask_text_model_with_context(
            context_with_instruction, question
        )

    return full_prompt, response, context, search_result


# Display a page showing logs
def show_logs(agent, template: str = "admin/logs.html"):
    docs_agent = agent
    product = docs_agent.config.product_name
    log_filename = "logs/chatui_logs.txt"
    answerable_log_filename = "logs/answerable_logs.txt"
    log_contents = ""
    answerable_contents = ""
    if docs_agent.config.enable_show_logs == "True":
        try:
            with open(log_filename, "r", encoding="utf-8") as file:
                log_contents = file.read()
        except:
            log_contents = "Cannot find or open log files."
        try:
            with open(answerable_log_filename, "r", encoding="utf-8") as file:
                answerable_contents = file.read()
        except:
            answerable_contents = (
                "Cannot find or open a file that contains answerable scores."
            )
    return render_template(
        template,
        product=product,
        logs=log_contents,
        answerable_logs=answerable_contents,
    )
