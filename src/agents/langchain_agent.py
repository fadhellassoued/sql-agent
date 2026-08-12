from langchain.agents import create_openai_tools_agent, AgentExecutor
from langchain.memory import ConversationBufferMemory
from langchain_groq import ChatGroq
from langchain.prompts import ChatPromptTemplate
from tools.langchain_tools import create_tools
import os

def create_agent(session_id: str, schema: dict, db_repo, knowledge_catalog, llm_service, memory=None):
    llm = ChatGroq(
        model="llama-3.1-8b-instant",
        temperature=0,
        api_key=os.getenv("GROQ_API_KEY")
    )

    tools = create_tools(session_id, schema, db_repo, knowledge_catalog, llm_service)

    if memory is None:
        memory = ConversationBufferMemory(memory_key="chat_history", return_messages=True)

    prompt = ChatPromptTemplate.from_messages([
        ("system", """You are a SQL performance expert. Your job is to help users optimize their SQL queries.

**IMPORTANT: You MUST use the tools provided. Your final answer MUST include the optimized SQL query.**

You have access to the following tools:
- detect_issues: ALWAYS call this first to find problems in the query.
- optimize_sql: ALWAYS call this to rewrite the query. This returns the optimized SQL.
- recommend_structures: Call this for long-term improvements.

**Your workflow:**
1. Call detect_issues to find problems.
2. Call optimize_sql to get the optimized SQL.
3. Call recommend_structures (optional).
4. **YOUR FINAL ANSWER MUST BE THE OPTIMIZED SQL QUERY** followed by a brief explanation.

Example:
- If optimize_sql returns: "SELECT id, name FROM users WHERE created_at > NOW() - INTERVAL 30 DAY LIMIT 100"
- Your final answer should be: "SELECT id, name FROM users WHERE created_at > NOW() - INTERVAL 30 DAY LIMIT 100" + explanation.

**DO NOT just say "Consider adding an index" – you MUST provide the actual optimized SQL.**"""),
        ("placeholder", "{chat_history}"),
        ("human", "{input}"),
        ("placeholder", "{agent_scratchpad}"),
    ])

    agent = create_openai_tools_agent(llm, tools, prompt)

    executor = AgentExecutor(
        agent=agent,
        tools=tools,
        memory=memory,
        verbose=True,
        handle_parsing_errors=True,
        max_iterations=5,
        return_intermediate_steps=True,  # ← Add this to see all steps
    )
    return executor