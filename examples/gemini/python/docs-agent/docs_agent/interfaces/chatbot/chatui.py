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
from bs4 import BeautifulSoup
import urllib
import os
from datetime import datetime
from pytz import timezone
from absl import logging
import pytz
import uuid

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
def construct_blueprint(product_config: config.ProductConfig):
    bp = Blueprint("chatui", __name__)
    if product_config.db_type == "google_semantic_retriever":
        docs_agent = DocsAgent(config=product_config, init_chroma=False)
    else:
        docs_agent = DocsAgent(config=product_config)
    logging.info(f"Launching the flask app for product: {product_config.product_name}")

    @bp.route("/", methods=["GET", "POST"])
    def index():
        server_url = request.url_root.replace("http", "https")
        return render_template(
            "chatui/index.html",
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
                    sources_ref,
                    plain_token,
                ) = ask_model_2_with_sources(input["question"], agent=docs_agent)
                source_array = []
                for source in sources_ref:
                    source_array.append(source.returnDictionary())
                dictionary = {
                    "response": response,
                    "full_prompt": full_prompt,
                    "token_count_context": plain_token,
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
            return redirect(url_for("chatui.index"))

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
            date = date.astimezone(timezone("US/Pacific"))
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
            if product_config.docs_agent_config == "experimental":
                return redirect(url_for("chatui.index_experimental"))
            elif product_config.docs_agent_config == "normal":
                return redirect(url_for("chatui.index"))

    # Render a response page when the user asks a question
    # using input text box.
    @bp.route("/result", methods=["GET", "POST"])
    def result():
        if request.method == "POST":
            question = request.form["question"]
            if product_config.docs_agent_config == "experimental":
                return ask_model2(question, agent=docs_agent, template="chatui/index_experimental.html")
            elif product_config.docs_agent_config == "normal":
                return ask_model2(
                    question, agent=docs_agent, template="chatui/index.html"
                )
        else:
            if product_config.docs_agent_config == "experimental":
                return redirect(url_for("chatui.index_experimental"))
            elif product_config.docs_agent_config == "normal":
                return redirect(url_for("chatui.index"))

    # Render a response page when the user clicks a question
    # from the related questions list.
    @bp.route("/question/<ask>", methods=["GET", "POST"])
    def question(ask):
        if request.method == "GET":
            question = urllib.parse.unquote_plus(ask)
            if product_config.docs_agent_config == "experimental":
                return ask_model2(question, agent=docs_agent, template="chatui/index_experimental.html")
            elif product_config.docs_agent_config == "normal":
                return ask_model2(
                    question, agent=docs_agent, template="chatui/index.html"
                )
        else:
            if product_config.docs_agent_config == "experimental":
                return redirect(url_for("chatui.index_expiremental"))
            elif product_config.docs_agent_config == "normal":
                return redirect(url_for("chatui.index"))

    return bp


# Construct a set of prompts using the user question, send the prompts to
# the lanaguage model, receive responses, and present them into a page.
# Use template to specify a custom template for the classic web UI
def ask_model2(question, agent, template: str = "chatui/index.html"):
    # Returns a built context, a total token count of the context and an array
    # of sourceOBJ
    full_prompt = ""
    final_context = ""
    docs_agent = agent
    new_question_count = 5
    results_num = 5
    aqa_response_in_html = ""
    if "gemini" in docs_agent.config.models.language_model:
        if docs_agent.config.docs_agent_config == "experimental":
            results_num = 10
            new_question_count = 5
        else:
            results_num = 5
            new_question_count = 5
        # Issue if max_sources > results_num, so leave the same for now
        search_result, final_context = docs_agent.query_vector_store_to_build(
            question=question,
            token_limit=30000,
            results_num=results_num,
            max_sources=results_num,
        )
        response, full_prompt = docs_agent.ask_content_model_with_context_prompt(
            context=final_context, question=question
        )
        aqa_response_in_html = ""
    elif "aqa" in docs_agent.config.models.language_model:
        if docs_agent.config.db_type == "google_semantic_retriever":
            (response, search_result) = docs_agent.ask_aqa_model_using_corpora(
                question=question
            )
        elif docs_agent.config.db_type == "chroma":
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
        # Buid a final_context item
        context_count = 0
        for item in search_result:
            context_count += 1
            final_context += (
                item.section.content + "\nReference [" + str(context_count) + "]\n\n"
            )
        final_context = final_context.strip()
        aqa_response_json = docs_agent.get_saved_aqa_response_json()
        if aqa_response_json:
            aqa_response_in_html = json.dumps(
                type(aqa_response_json).to_dict(aqa_response_json), indent=2
            )

    ### PROMPT: GET RELATED QUESTIONS.
    # 1. Use the response from Prompt 1 as context and add a custom condition.
    # 2. Prepare a new question asking the model to come up with 5 related questions.
    # 3. Ask the language model with the new question.
    # 4. Parse the model's response into a list in HTML format.
    new_condition = f"Read the context below and answer the question at the end:"
    new_question = f"Can you think of {new_question_count} questions whose answers can be found in the context above?"
    (
        related_questions_response,
        new_prompt_questions,
    ) = docs_agent.ask_content_model_with_context_prompt(
        context=final_context, question=new_question, prompt=new_condition
    )
    # Clean up the response to a proper html list
    related_questions = parse_related_questions_response_to_html_list(
        markdown.markdown(related_questions_response)
    )

    ### PREPARE OTHER ELEMENTS NEEDED BY UI.
    # - Create a uuid for this request.
    # - A workaround to get the server's URL to work with the rewrite and like features.
    new_uuid = uuid.uuid1()
    server_url = request.url_root.replace("http", "https")
    ### Check the AQA model's answerable_probability field
    probability = "None"
    if docs_agent.check_if_aqa_is_used():
        aqa_response = docs_agent.get_saved_aqa_response_json()
        try:
            probability = aqa_response.answerable_probability
        except:
            probability = 0.0
    ### LOG THIS REQUEST.
    log_question(new_uuid, question, response, probability)

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
    )


# Not fully implemented
# This method is used for the API endpoint, so it returns values that can be
# packaged as JSON
def ask_model_2_with_sources(question, agent):
    docs_agent = agent
    full_prompt = ""
    context, plain_token, sources_ref = docs_agent.query_vector_store_to_build(
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

    return full_prompt, response, context, sources_ref, plain_token
