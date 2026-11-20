procedure Body;
begin
  ResetParameters; AddStringParameter('Action', 'Document'); RunProcess('Sch:Zoom');
  L('ok');
end;
