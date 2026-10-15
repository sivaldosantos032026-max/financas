# Finanças pessoais

Aplicação simples de gestão financeira com Streamlit e Firebase Firestore. Permite cadastrar, consultar, editar e excluir receitas e despesas, alterar a categoria dos lançamentos, acompanhar o saldo, filtrar lançamentos por período e emitir relatório com data e hora. O painel também exibe um mapa de calor mensal dos gastos por categoria.

## Configuração

1. No Console do Firebase, abra o projeto e confirme o **ID do projeto** em Configurações do projeto.
2. Em **Contas de serviço**, gere uma chave privada para o Firebase Admin SDK. Guarde o arquivo com segurança e não o envie ao Git.
3. Confirme que o Firestore está habilitado e que o banco de dados selecionado é o `(default)`.
4. O aplicativo Web **Finanças pessoais** já foi registrado no projeto. Em **Configurações do projeto > Geral > Seus apps**, localize-o e copie os campos `apiKey`, `authDomain` e `appId` caso precise configurar outra instalação.
5. Instale as dependências:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

6. Os campos da configuração do app Web já foram adicionados à seção `[firebase]` do `.streamlit\secrets.toml` local. Preserve a conta de serviço existente nesse arquivo ao atualizá-lo e use o mesmo ID nos campos `project_id`.
7. Em **Authentication > Sign-in method**, confirme que Google está habilitado. Em **Authentication > Settings > Authorized domains**, autorize `localhost` para desenvolvimento e o domínio publicado em produção.
8. Inicie a aplicação:

   ```powershell
   .\.venv\Scripts\python.exe -m streamlit run app.py
   ```

O login usa diretamente o provedor Google do Firebase Authentication. Uma sessão Firebase já ativa no navegador é restaurada automaticamente com persistência local; na primeira entrada, clique em **Entrar com Google** e selecione sua conta. O botão **Sair** encerra a sessão. O servidor verifica o ID token do Firebase antes de permitir acesso. Cada lançamento novo recebe o UID autenticado e só é consultado, alterado ou excluído pelo respectivo usuário.

Na primeira autenticação, o UID que for registrado como proprietário dos dados antigos recebe os lançamentos preexistentes sem `owner_uid`; a migração é marcada como concluída no Firestore. Os usuários seguintes veem somente os próprios lançamentos. Para evitar que uma pessoa não autorizada assuma os dados legados, inicie o app em ambiente controlado e faça primeiro login com a conta proprietária pretendida.

Os dados são gravados na coleção `lancamentos_financeiros` do banco `(default)`. Os valores são guardados em centavos para evitar erros de arredondamento monetário. O filtro de datas inicia nos últimos 12 meses e pode ser alterado na barra lateral. O alerta avisa quando as despesas do mês corrente atingem R$ 1.500,00. Lançamentos antigos sem hora usam a hora de criação como referência no relatório.

## Segurança

O arquivo `.streamlit/secrets.toml` contém uma credencial administrativa privilegiada e está ignorado pelo Git. Não compartilhe nem publique essa chave. Embora o `api_key` da configuração Web seja identificador público do app, a chave privada da conta de serviço deve permanecer somente no servidor. Firestore é acessado pelo Admin SDK, que ignora as regras de segurança do Firestore; portanto, as operações do servidor filtram e verificam o UID Firebase autenticado antes de ler ou alterar documentos.
