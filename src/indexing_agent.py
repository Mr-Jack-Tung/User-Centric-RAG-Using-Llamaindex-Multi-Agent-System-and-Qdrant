from dotenv import load_dotenv
import os
import json
from fastembed import SparseTextEmbedding, TextEmbedding
from qdrant_client import QdrantClient, models
from qdrant_client.http.models import PointStruct, SparseVector
from tqdm import tqdm

from typing import List
import pprint

from llama_index.core.memory import ChatMemoryBuffer
from llama_index.core.tools import FunctionTool
from llama_index.llms.openai import OpenAI 
from llama_index.agent.openai import OpenAIAgent
from reranking_agent import ReRankingAgent


# Load environmental variables from a .env file
load_dotenv()

Qdrant_API_KEY = os.getenv('Qdrant_API_KEY')
Qdrant_URL = os.getenv('Qdrant_URL')
Collection_Name = os.getenv('collection_name')
qdrant_client = QdrantClient(
                            url=Qdrant_URL,
                            api_key=Qdrant_API_KEY)
        
        
def load_nodes():
    metadata = []
    documents = []
    payload_file = r'..\data\nodes.json'

    try:
        with open(payload_file, 'r') as file:
            nodes = json.load(file)

        for node in nodes:
            metadata.append(node['metadata'])
            documents.append(node['text'])

        print(f"Loaded {len(nodes)} the nodes from JSON file")

    except Exception as e:
        print(f"Error loading nodes from JSON file: {e}")
        raise

    return documents, metadata

def client_collection():
    """
    Create a collection in Qdrant vector database.
    """
    
    if not qdrant_client.collection_exists(collection_name=Collection_Name): 
        qdrant_client.create_collection(
            collection_name= Collection_Name,
            vectors_config={
                    'dense': models.VectorParams(
                        size=384,
                        distance = models.Distance.COSINE,
                    )
            },
            sparse_vectors_config={
                "sparse": models.SparseVectorParams(
                            index=models.SparseIndexParams(
                            on_disk=False,              
                        ),
                    )
                }
        )
        
    print(f"Created collection '{Collection_Name}' in Qdrant vector database.")


def create_sparse_vector(sparse_embedding_model, text):
    """
    Create a sparse vector from the text using SPLADE.
    """
    sparse_embedding_model = sparse_embedding_model
    # Generate the sparse vector using SPLADE model
    embeddings = list(sparse_embedding_model.embed([text]))[0]

    # Check if embeddings has indices and values attributes
    if hasattr(embeddings, 'indices') and hasattr(embeddings, 'values'):
        sparse_vector = models.SparseVector(
            indices=embeddings.indices.tolist(),
            values=embeddings.values.tolist()
        )
        return sparse_vector
    else:
        raise ValueError("The embeddings object does not have 'indices' and 'values' attributes.")

Embeddings = {
    "sentence-transformer": "sentence-transformers/all-MiniLM-L6-v2",
    "snowflake": "Snowflake/snowflake-arctic-embed-m",
    "BAAI": "BAAI/bge-large-en-v1.5",
}

def insert_documents(embedding_model, documents, metadata):
    points = []
    embedding_model = TextEmbedding(model_name=Embeddings[embedding_model])
    sparse_embedding_model = SparseTextEmbedding(model_name="Qdrant/bm42-all-minilm-l6-v2-attentions")
    for i, (doc, metadata) in enumerate(tqdm(zip(documents, metadata), total=len(documents))):
        # Generate both dense and sparse embeddings
        dense_embedding = list(embedding_model.embed([doc]))[0]
        sparse_vector = create_sparse_vector(sparse_embedding_model, doc)

        # Create PointStruct
        point = models.PointStruct(
            id=i,
            vector={
                'dense': dense_embedding.tolist(),
                'sparse': sparse_vector,
            },
            payload={
                'text': doc,
                **metadata  # Include all metadata
            }
        )
        points.append(point)

    # Upsert points
    qdrant_client.upsert(
        collection_name=Collection_Name,
        points=points
    )

    print(f"Upserted {len(points)} points with dense and sparse vectors into Qdrant vector database.")

class Indexing:
    def __init__(self, state: dict):
        self.state = state
        self.embedding_model = state.get('embedding_model')
    
    def indexing(self) -> None:
        """
        Index the documents into the Qdrant vector database.
        """
        print("Starting to load the nodes from JSON file")
        documents, metadata = load_nodes()
        client_collection()
        print("Creation of the Qdrant Collection is Done")
        insert_documents(self.embedding_model, documents, metadata)
        print("Indexing of the nodes is complete")

    
def QdrantIndexingAgent(state: dict) -> OpenAIAgent:  

    def has_embedding_model(embedding_model: str) -> bool:
        """Useful for checking if the user has specified an embedding model."""
        print("Orchestrator checking if embedding model is specified")
        state['embedding_model'] = embedding_model
        return (state["embedding_model"] is not None)

    def done() -> None:
        """When you inserted the vetors into the Qdrant Cluster, call this tool."""
        logging.info("Indexing of the nodes is complete and updating the state")
        state["current_speaker"] = None
        state["just_finished"] = True
    
    Index = Indexing(state)
    tools = [
        FunctionTool.from_defaults(fn = has_embedding_model),
        FunctionTool.from_defaults(fn=Index.indexing),
        FunctionTool.from_defaults(fn=done),
    ]

    system_prompt = (f"""
    You are a helpful assistant responsible for indexing nodes in a retrieval-augmented generation (RAG) system.
    Your task is to index these nodes into a Qdrant cluster.
    To proceed, you need to know which embedding model to use.
    
    * If the user intends to index the nodes but has not specified an embedding model (has_embedding_model is false), kindly prompt the user to provide the embedding model.
    
    Once the embedding model is provided, use the tool "Index.indexing" with the specified embedding model to index the nodes into the Qdrant cluster.
    The current user state is:
    {pprint.pformat(state, indent=4)}
    After successfully indexing the nodes into the Qdrant cluster, call the tool "done" to signal the completion of your task.
    If the user requests a task other than indexing the nodes, call the tool "done" to indicate that another agent should assist.
    """)

    return OpenAIAgent.from_tools(
        tools,
        llm=OpenAI(model="gpt-3.5-turbo"),
        system_prompt=system_prompt,
    )

if __name__ == '__main__':
    state= {   'chunk_overlap': None,
    'chunk_size': None,
    'current_speaker': 'indexing',
    'embedding_model': None,
    'input_dir': None,
    'just_finished': False,
    'query': None,
    'reranking_model': None,
    'search_type': None}
    agent = QdrantIndexingAgent(state = state)
    response = agent.chat("I want to index the nodes into the vector database using the sentence-transformer embedding model.")