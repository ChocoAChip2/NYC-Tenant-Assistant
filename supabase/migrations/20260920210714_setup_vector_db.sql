-- 1. Enable the pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Create the table to store your NYC legal texts
CREATE TABLE legal_documents (
    id BIGSERIAL PRIMARY KEY,
    source_name TEXT NOT NULL,
    text_content TEXT NOT NULL,
    -- Gemini's text-embedding-004 model outputs 768 dimensions by default
    embedding vector(768) 
);

-- 3. Create an HNSW index to make searching massive amounts of text extremely fast
CREATE INDEX ON legal_documents USING hnsw (embedding vector_cosine_ops);

-- 4. Create a Postgres function (RPC) to perform the similarity search
CREATE OR REPLACE FUNCTION match_legal_documents(
    query_embedding vector(768),
    match_threshold float,
    match_count int
)
RETURNS TABLE (
    id bigint,
    source_name text,
    text_content text,
    similarity float
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        legal_documents.id,
        legal_documents.source_name,
        legal_documents.text_content,
        1 - (legal_documents.embedding <=> query_embedding) AS similarity
    FROM legal_documents
    -- <=> is the pgvector operator for cosine distance
    WHERE 1 - (legal_documents.embedding <=> query_embedding) > match_threshold
    ORDER BY legal_documents.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;
