from datetime import datetime
from pathlib import Path
import re, uuid, json

class KnowledgeManager:
    """외부 벡터DB 의존 없이 동작하는 V1.0 로컬 지식 저장/검색 계층."""

    def __init__(self,database,chunk_size=1200):
        self.database=database; self.chunk_size=chunk_size

    def add_document(self,source_path,title=None,content=None,project_id=None,metadata=None):
        p=Path(source_path)
        if content is None: content=p.read_text(encoding="utf-8")
        doc="DOC-"+str(uuid.uuid4())[:8].upper()
        title=title or p.name
        now=datetime.now().isoformat()
        conn=self.database.connect()
        conn.execute("INSERT INTO knowledge_documents VALUES (?,?,?,?,?,?,?)",
                     (doc,project_id,str(p),title,content,json.dumps(metadata or {},ensure_ascii=False),now))
        chunks=[content[i:i+self.chunk_size] for i in range(0,len(content),self.chunk_size)] or [""]
        for idx,ch in enumerate(chunks):
            conn.execute("INSERT INTO knowledge_chunks VALUES (?,?,?,?,?)",
                         ("CH-"+str(uuid.uuid4())[:8].upper(),doc,idx,ch,now))
        conn.commit(); conn.close()
        return doc

    def index_file(self,path,project_id=None):
        return self.add_document(path,project_id=project_id)

    def search(self,query,top_k=5,project_id=None):
        terms=[t.lower() for t in re.findall(r"\w+",query,flags=re.UNICODE) if len(t)>1]
        conn=self.database.connect()
        sql="""SELECT c.chunk_id,c.document_id,c.content,d.title,d.source_path
               FROM knowledge_chunks c JOIN knowledge_documents d ON d.document_id=c.document_id"""
        args=[]
        if project_id is not None:
            sql+=" WHERE d.project_id=?"; args.append(project_id)
        rows=conn.execute(sql,args).fetchall(); conn.close()
        scored=[]
        for row in rows:
            text=row[2].lower()
            score=sum(text.count(t) for t in terms)
            if score: scored.append((score,row))
        scored.sort(key=lambda x:(x[0],x[1][0]),reverse=True)
        return [{"score":s,"chunk_id":r[0],"document_id":r[1],"content":r[2],"title":r[3],"source_path":r[4]} for s,r in scored[:top_k]]

    def build_context(self,query,top_k=5,project_id=None):
        return self.search(query,top_k,project_id)
